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
MODEL = Settings().embedding_model  # text-embedding-3-large, cut to 1536 dimensions
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


class DefaultsTests(unittest.TestCase):
    def test_large_model_at_1536_dimensions_with_the_measured_floor(self):
        settings = Settings()
        self.assertEqual(settings.embedding_model, "text-embedding-3-large")
        self.assertEqual(settings.vector_min_similarity, 0.27)
        self.assertEqual(EMBEDDING_DIMENSIONS, 1536)  # the column is vector(1536)


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


class NoInformationAnswerTests(_Stored):
    """An answer that says it knows nothing lists no sources and offers no places, when
    the only documents behind it were merely similar to the question."""

    OFFER = {"type": "add", "target": CAVE, "reason": "카페 대신 가 볼 만해요", "time_bound": False}

    def answer(self, reply, message=QUESTION, sources=(CAVE,), proposals=(OFFER,),
               intent="proposal", **context):
        said = {"intent": intent, "reply": reply, "chips": ["동해역 근처", "다음"],
                "proposals": [dict(p) for p in proposals], "sources": list(sources),
                "tool_request": None}
        with patch.object(llm, "chat_json", return_value=said):
            response = self.client.post(URL, json={"message": message, "context": context})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def setUp(self):
        super().setUp()
        self.fake_openai(one_hot(self.axis[CAVE]))  # the cave is what similarity finds

    def test_no_information_with_similar_documents_and_offers_is_emptied(self):
        for reply in ("강릉 쪽 카페는 아직 확인된 정보가 없어서 추천을 못 드려요. 천곡황금박쥐동굴은 어때요?",
                      "부산 맛집은 아직 확인된 정보로 추천해 드리기 어려워요. 대신 동굴을 넣어 드릴게요.",
                      "아직 확인된 정보가 없어요. 대신 천곡황금박쥐동굴을 추천해요."):
            with self.subTest(reply):
                body = self.answer(reply, message="강릉에 분위기 좋은 카페 있어?")
                self.assertEqual((body["sources"], body["proposals"]), ([], []))
                self.assertIn("확인된 정보가 없어요", body["reply"])
                self.assertNotIn("동굴", body["reply"])
                self.assertEqual(body["chips"][0], "동해역 근처")  # chips that lead somewhere stay

    def test_offers_alone_are_emptied_too(self):
        body = self.answer("영화 추천은 아직 확인된 정보가 없어요. 대신 동굴은 어때요?",
                           message="볼 만한 영화 추천해줘", sources=())
        self.assertEqual((body["sources"], body["proposals"]), ([], []))

    def test_a_real_answer_from_a_similar_document_keeps_it(self):
        body = self.answer("비 오는 날에는 천곡황금박쥐동굴이 좋아요. 실내 관람지예요.",
                           message="비 오는 날 실내에서 볼 만한 곳 있어?", proposals=())
        self.assertEqual(body["sources"], [CAVE])
        self.assertIn("천곡황금박쥐동굴", body["reply"])

    def test_no_information_later_in_a_real_answer_keeps_it(self):
        body = self.answer("비 오는 날에는 천곡황금박쥐동굴이 좋아요. 입장료는 아직 확인된 정보가 없어요.",
                           message="비 오는 날 실내에서 볼 만한 곳 있어?")
        self.assertEqual(body["sources"], [CAVE])
        self.assertEqual([p["target"] for p in body["proposals"]], [CAVE])

    def test_a_partial_answer_about_a_named_place_keeps_its_source(self):
        body = self.answer("아직 확인된 정보가 없어요. 천곡황금박쥐동굴 입장료는 문서에 없어서요.",
                           message="천곡황금박쥐동굴 입장료 얼마야?", proposals=(),
                           intent="knowledge")
        self.assertEqual(body["sources"], [CAVE])  # found by its name, not by similarity

    def test_a_no_information_answer_with_nothing_attached_is_left_as_written(self):
        reply = "부산역은 지도에 없어서 아직 확인된 정보가 없어요. 지도에서 역을 열어 주세요."
        body = self.answer(reply, message="부산역 시간표 알려줘", sources=(), proposals=(),
                           intent="train_lookup")
        self.assertEqual(body["reply"], reply)


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
