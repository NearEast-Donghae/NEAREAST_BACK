"""Vector search (retrieval step 3): what is embedded, and the guards around using it.

Each guard has a case where it must bite and one where it must not. No network: the
embedding client is a fake, and documents get one-hot vectors so similarities are exact.
"""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from legacy_support import create_app
from openai import APITimeoutError, OpenAIError

from neareast_api.core.config import Settings
from neareast_api.db.content_repository import ContentRepository
from neareast_api.db.entities import KnowledgeDocumentEntity, PlaceEntity
from neareast_api.db.session import CONTENT_SESSION
from neareast_api.ingestion.service import IngestionService
from neareast_api.modules.chat import llm, retrieval
from neareast_api.modules.chat.retrieval import EMBEDDING_DIMENSIONS, Content, Doc
from neareast_api.modules.places.service import Place

URL = "/api/v1/chat/messages"
MODEL = "text-embedding-3-small"
MARKET, CAVE, CHANGES = "poi_bukpyeong_market", "poi_cheongok_cave", "rail_2031_changes"
QUESTION = "곧 없어질 경치가 있다던데"  # names no document: only the vector step can find one


def one_hot(*indexes, size=EMBEDDING_DIMENSIONS):
    vector = [0.0] * size
    for index in indexes:
        vector[index] = 1.0
    return vector


class _Stored(unittest.TestCase):
    """A seeded database whose documents are embedded as one-hot vectors (document i -> axis i)."""

    settings = Settings(chat_mock=False, openai_api_key="test", llm_usage_log=os.devnull)
    stored_model = MODEL

    def setUp(self):
        self.app = create_app(self.settings)
        self.session = self.app.state.sessionFactory()
        token = CONTENT_SESSION.set(self.session)

        def close():
            CONTENT_SESSION.reset(token)
            self.session.close()
            self.app.state.databaseEngine.dispose()
        self.addCleanup(close)
        self.sent_texts = {}

        def embed_documents(model, texts):
            ids = [row.id for row in self.session.query(KnowledgeDocumentEntity)
                   .order_by(KnowledgeDocumentEntity.id)]
            self.axis = {doc_id: index for index, doc_id in enumerate(ids)}
            self.sent_texts.update(zip(ids, texts, strict=True))
            return [one_hot(index) for index in range(len(texts))]
        self.ingested = IngestionService.embedDocuments(
            self.session, self.stored_model, embed_documents)
        llm.clear_embedding_cache()
        self.addCleanup(llm.clear_embedding_cache)
        self.client = TestClient(self.app)

    def fake_openai(self, vector=None, error=None):
        """Stands in for the OpenAI client; records every embeddings.create call."""
        calls = []

        def create(**kwargs):
            calls.append({**kwargs, "timeout": options["timeout"]})
            if error is not None:
                raise error
            usage = SimpleNamespace(prompt_tokens=5, total_tokens=5)
            return SimpleNamespace(data=[SimpleNamespace(embedding=vector)], usage=usage)
        options = {}

        def with_options(**kwargs):
            options.update(kwargs)
            return SimpleNamespace(embeddings=SimpleNamespace(create=create))
        patcher = patch.object(llm, "_client_for",
                               return_value=SimpleNamespace(with_options=with_options))
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def ask(self, message=QUESTION, **context):
        """Returns (response body, ids of the documents the model was given)."""
        answer = {"intent": "knowledge", "reply": "좋아요.", "chips": ["다음"], "proposals": [],
                  "sources": [], "tool_request": None}
        with patch.object(llm, "chat_json", return_value=answer) as chat:
            response = self.client.post(URL, json={"message": message, "context": context})
        self.assertEqual(response.status_code, 200, response.text)
        prompt = "\n".join(m["content"] for m in chat.call_args.args[1])
        given = [doc_id for doc_id in self.axis if f"<{doc_id}>" in prompt]
        return response.json(), given


class EmbeddingTextTests(_Stored):
    def test_text_is_title_aliases_related_place_names_and_body(self):
        places = {"station_mukho": Place("station_mukho", "묵호역", "station", aliases=("묵호",)),
                  "poi_nongoldam": Place("poi_nongoldam", "논골담길", "poi", aliases=("논골담",))}
        doc = Doc("station_mukho", "묵호역", "station", ("station_mukho", "poi_nongoldam", "gone"),
                  ("묵호역", "묵호"), "묵호역은 1961년에 문을 열었어요.")
        self.assertEqual(retrieval.embedding_text(doc, places),
                         "묵호역\n별칭: 묵호역, 묵호, 논골담\n관련 장소: 묵호역, 논골담길\n"
                         "묵호역은 1961년에 문을 열었어요.")
        bare = Doc("rail_x", "노선", "rail", (), (), "본문")
        self.assertEqual(retrieval.embedding_text(bare, places), "노선\n본문")

    def test_ingestion_embeds_that_text_for_every_document(self):
        self.assertEqual(self.ingested, {"pending": [], "embedded": 20})
        text = self.sent_texts[MARKET]
        for part in ("북평민속시장", "별칭: ", "오일장", "북평장", "관련 장소: 북평민속시장", "1796년"):
            self.assertIn(part, text)
        content = ContentRepository.knowledge()
        self.assertEqual(text, retrieval.embedding_text(content.docs[MARKET], content.places))
        self.assertEqual((content.embedding_model, len(content.vectors)), (MODEL, 20))

    def test_only_documents_whose_text_changed_are_embedded_again(self):
        self.assertEqual(IngestionService.embedDocuments(self.session, MODEL)["pending"], [])
        place = self.session.get(PlaceEntity, MARKET)
        place.aliases = [*place.aliases, "북평오일장"]  # an alias is part of the embedded text
        self.session.commit()
        self.assertEqual(IngestionService.embedDocuments(self.session, MODEL)["pending"], [MARKET])
        # Until it is embedded again the old vector is not used; the others still are.
        vectors = ContentRepository.knowledge().vectors
        self.assertNotIn(MARKET, vectors)
        self.assertEqual(len(vectors), 19)
        again = IngestionService.embedDocuments(
            self.session, MODEL, lambda model, texts: [one_hot(0) for _ in texts])
        self.assertEqual(again["embedded"], 1)
        self.assertIn(MARKET, ContentRepository.knowledge().vectors)

    def test_another_model_embeds_everything_again(self):
        self.assertEqual(len(IngestionService.embedDocuments(self.session, "other")["pending"]), 20)


class SimilarityFloorTests(unittest.TestCase):
    def setUp(self):
        docs = {name: Doc(name, name, "rail", (), (), "본문") for name in ("near", "half", "far")}
        self.content = Content({}, docs, "m", {"near": (1.0, 0.0), "half": (1.0, 1.0),
                                               "far": (0.0, 1.0)})
        self.embed = lambda text: ("m", (1.0, 0.0))

    def found(self, **kwargs):
        return [(h.doc.id, round(h.score, 2)) for h in
                self.content.search("뜻으로 묻기", embed=self.embed, **kwargs)]

    def test_documents_at_or_above_the_floor_are_kept_best_first(self):
        self.assertEqual(self.found(), [("near", 1.0), ("half", 0.71), ("far", 0.0)])
        self.assertEqual(self.found(min_similarity=0.70), [("near", 1.0), ("half", 0.71)])

    def test_documents_below_the_floor_are_dropped(self):
        self.assertEqual(self.found(min_similarity=0.8), [("near", 1.0)])
        self.assertEqual(self.found(min_similarity=1.0 + 1e-9), [])

    def test_the_floor_does_not_touch_context_and_keyword_hits(self):
        hits = self.content.search("far 알려줘", context_ids=["half"], embed=self.embed,
                                   min_similarity=0.99)
        self.assertEqual([(h.doc.id, h.reason) for h in hits],
                         [("half", "context"), ("far", "keyword"), ("near", "vector")])


class GuardTests(_Stored):
    def test_vector_step_finds_a_document_the_question_does_not_name(self):
        calls = self.fake_openai(one_hot(self.axis[CHANGES]))
        body, given = self.ask()
        self.assertEqual(given, [CHANGES])
        self.assertEqual(body["mode"], "llm")
        self.assertEqual(len(calls), 1)
        self.assertEqual((calls[0]["model"], calls[0]["input"], calls[0]["dimensions"]),
                         (MODEL, [QUESTION], 1536))

    def test_similarity_floor_from_settings(self):
        # 0.71 similar to two documents: used at the default floor, dropped at 0.8.
        self.fake_openai(one_hot(self.axis[CHANGES], self.axis[CAVE]))
        self.assertEqual(sorted(self.ask()[1]), sorted([CHANGES, CAVE]))
        strict = self.settings.model_copy(update={"vector_min_similarity": 0.8})
        self.client = TestClient(self._app_with(strict))
        body, given = self.ask()
        self.assertEqual(given, [])
        self.assertIn("확인된 정보가 없어요", body["reply"])  # nothing to go by: says so

    def _app_with(self, settings):
        self.app.state.settings = settings
        return self.app

    def test_embedding_failure_is_answered_without_vectors_not_with_an_error(self):
        calls = self.fake_openai(error=OpenAIError("down"))
        with self.assertLogs("neareast_api.modules.chat.service", "WARNING") as logs:
            body, given = self.ask("여기 뭐야?", focused="station_donghae")
        self.assertEqual(len(calls), 1)
        self.assertEqual(body["mode"], "llm")  # the model still answered, from steps 1 and 2
        self.assertIn("station_donghae", given)
        self.assertTrue(any("vector search skipped" in line for line in logs.output))

    def test_a_slow_embedding_is_cut_short_and_skipped(self):
        import httpx
        timeout = APITimeoutError(request=httpx.Request("POST", "https://example.invalid"))
        calls = self.fake_openai(error=timeout)
        body, given = self.ask()
        self.assertEqual((body["mode"], given), ("llm", []))
        # Its own short limit, not the 8 seconds a chat completion may take.
        self.assertEqual(calls[0]["timeout"], self.settings.embedding_timeout_seconds)
        self.assertLess(calls[0]["timeout"], self.settings.llm_timeout_seconds)

    def test_the_same_question_is_embedded_once(self):
        calls = self.fake_openai(one_hot(self.axis[CHANGES]))
        self.ask()
        self.ask()
        self.assertEqual(len(calls), 1)
        self.ask("다른 질문이에요")
        self.assertEqual(len(calls), 2)

    def test_a_failed_embedding_is_not_remembered(self):
        failing = self.fake_openai(error=OpenAIError("down"))
        self.ask()
        self.assertEqual(len(failing), 1)
        working = self.fake_openai(one_hot(self.axis[CHANGES]))
        self.assertEqual(self.ask()[1], [CHANGES])
        self.assertEqual(len(working), 1)

    def test_switched_off_means_no_embedding_call(self):
        calls = self.fake_openai(one_hot(self.axis[CHANGES]))
        self.client = TestClient(self._app_with(
            self.settings.model_copy(update={"chat_vector_search": False})))
        self.assertEqual(self.ask()[1], [])
        self.assertEqual(calls, [])


class ModelMismatchTests(_Stored):
    stored_model = "older-embedding-model"

    def test_vectors_from_another_model_are_not_used_and_it_is_logged(self):
        calls = self.fake_openai(one_hot(0))
        with self.assertLogs("neareast_api.modules.chat.retrieval", "WARNING") as logs:
            body, given = self.ask()
        self.assertEqual((calls, given), ([], []))  # no embedding call is spent either
        self.assertEqual(body["mode"], "llm")
        self.assertTrue(any("older-embedding-model" in line and MODEL in line
                            for line in logs.output))

    def test_matching_model_is_used(self):
        matching = self.settings.model_copy(update={"embedding_model": "older-embedding-model"})
        self.app.state.settings = matching
        calls = self.fake_openai(one_hot(self.axis[CHANGES]))
        self.assertEqual(self.ask()[1], [CHANGES])
        self.assertEqual(calls[0]["model"], "older-embedding-model")


class NoVectorsTests(unittest.TestCase):
    def test_without_stored_vectors_nothing_is_embedded(self):
        settings = Settings(chat_mock=False, openai_api_key="test", llm_usage_log=os.devnull)
        client = TestClient(create_app(settings))
        answer = {"intent": "knowledge", "reply": "좋아요.", "chips": [], "proposals": [],
                  "sources": [], "tool_request": None}
        with patch.object(llm, "chat_json", return_value=answer), \
                patch.object(llm, "embed_query") as embed:
            response = client.post(URL, json={"message": QUESTION})
        self.assertEqual(response.status_code, 200)
        embed.assert_not_called()


if __name__ == "__main__":
    unittest.main()
