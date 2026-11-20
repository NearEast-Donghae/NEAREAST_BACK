"""Answers outside what Nearie knows, answers the model forgot to cite, and the basis of
train times said once. Each rule both ways: when it applies and when it must not."""

import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from legacy_support import create_app
from legacy_support import setUpModule as setUpModule
from legacy_support import tearDownModule as tearDownModule

from neareast_api.core.config import Settings
from neareast_api.modules.chat import llm, retrieval, service

URL = "/api/v1/chat/messages"
DONGHAE, MUKHO, CAVE, MARKET = (
    "station_donghae", "station_mukho", "poi_cheongok_cave", "poi_bukpyeong_market")
ADD_CAVE = {"type": "add", "target": CAVE, "reason": "가 볼 만해요", "time_bound": False}


def model(reply, intent="knowledge", sources=(), proposals=(), tool=None):
    return {"intent": intent, "reply": reply, "chips": ["동해역 근처", "다음"],
            "proposals": list(proposals), "sources": list(sources), "tool_request": tool,
            "plan": None}


class _Case(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(chat_mock=False, openai_api_key="test", llm_usage_log=os.devnull)
        self.client = TestClient(create_app(self.settings))

    def ask(self, outputs, message, vector_docs=(), **context):
        """`vector_docs`: documents the search returns by similarity only."""
        real = retrieval.Content.search

        def search(content, text, ids, **kwargs):
            hits = real(content, text, ids)
            return [*hits, *(retrieval.Hit(content.docs[d], "vector", 0.4) for d in vector_docs
                             if all(h.doc.id != d for h in hits))]

        with patch.object(llm, "chat_json", side_effect=list(outputs)), \
                patch.object(retrieval.Content, "search", search):
            response = self.client.post(URL, json={"message": message, "context": context})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()


class OutsideAreaTests(_Case):
    """Decided by what is asked, not by how the model words its answer."""

    offer = "강릉 쪽은 잘 모르지만 대신 동해에서 분위기 좋은 곳을 몇 군데 알려 드릴게요."

    def test_which_questions_are_about_somewhere_else(self):
        content = retrieval.load_content(self.settings.content_dir)
        outside = {"부산역 맛집 알려줘": "부산", "강릉에 분위기 좋은 카페 있어?": "강릉",
                   "제주도 맛집 추천해줘": "제주", "일본 여행 어디가 좋아?": "일본"}
        for message, region in outside.items():
            with self.subTest(message):
                self.assertEqual(service.outside_area(message, content), region)
        ours = ("서울에서 오는 열차 몇 시야?", "강릉행 열차 언제 있어?", "부산 가는 기차 있어?",
                "강릉에서 왔는데 동해역 근처에 뭐 있어?", "논골담길 어때?", "비 오는 날 갈 만한 데",
                "서울에서 출발해서 묵호역 볼 거야")
        for message in ours:
            with self.subTest(message):
                self.assertIsNone(service.outside_area(message, content))

    def test_an_offer_of_our_places_for_another_region_is_not_served(self):
        # Seen on the real model: no "확인된 정보가 없어요" wording, sources and proposals attached.
        body = self.ask([model(self.offer, "proposal", sources=[CAVE], proposals=[ADD_CAVE])],
                        "강릉에 분위기 좋은 카페 있어?", vector_docs=[CAVE])
        self.assertEqual(body["reply"], f"{service.NO_INFO} {service.OUT_OF_AREA}")
        self.assertEqual((body["sources"], body["proposals"]), ([], []))
        self.assertEqual(body["intent"], "knowledge")
        self.assertEqual(body["chips"][0], "동해역 근처")  # chips that lead somewhere stay

    def test_a_train_question_about_another_city_is_answered(self):
        tool = {"name": "search_trains", "station_id": DONGHAE, "place_id": None, "after": None}
        body = self.ask([model("확인해 볼게요.", "train_lookup", tool=tool),
                         model("동해역에서 강릉행 열차가 있어요.", "train_lookup")],
                        "강릉행 열차 언제 있어?", focused=DONGHAE)
        self.assertIn("강릉행 열차가 있어요", body["reply"])
        self.assertEqual(body["tool_used"], "search_trains")

    def test_another_city_next_to_one_of_our_places_is_answered(self):
        body = self.ask([model("동해역은 분기역이에요.", sources=[DONGHAE])],
                        "강릉에서 왔는데 동해역은 어떤 역이야?")
        self.assertEqual(body["reply"], "동해역은 분기역이에요.")
        self.assertEqual(body["sources"], [DONGHAE])


class UnrelatedDocumentsTests(_Case):
    """Documents that only resemble the question, when the question names a place."""

    def test_similar_documents_not_about_the_named_place_are_not_an_answer(self):
        body = self.ask([model("수영장은 천곡황금박쥐동굴 쪽이 좋아요.", sources=[CAVE],
                               proposals=[ADD_CAVE])],
                        "묵호역에 수영장 있어?", vector_docs=[CAVE])
        self.assertEqual(body["reply"], service.NO_INFO)  # in the area: no "only 동해" line
        self.assertEqual((body["sources"], body["proposals"]), ([], []))

    def test_a_document_about_the_named_place_stands(self):
        body = self.ask([model("묵호역 근처에 논골담길이 있어요.", sources=[MUKHO])],
                        "묵호역 근처에 뭐가 있어?")
        self.assertEqual(body["sources"], [MUKHO])
        self.assertIn("논골담길", body["reply"])

    def test_a_similar_document_about_the_named_place_stands(self):
        # 논골담길's document is about 묵호역 too (its places list it).
        body = self.ask([model("묵호역에서 가까운 논골담길이 걷기 좋아요.", sources=["poi_nongoldam"])],
                        "묵호역에서 걷기 좋은 데", vector_docs=["poi_nongoldam"])
        self.assertEqual(body["sources"], ["poi_nongoldam"])

    def test_a_question_that_names_no_place_is_answered_by_meaning(self):
        body = self.ask([model("천곡황금박쥐동굴은 실내라 비 오는 날에도 좋아요.", sources=[CAVE])],
                        "비 오는 날 갈 만한 실내", vector_docs=[CAVE])
        self.assertEqual(body["sources"], [CAVE])
        # The open marker does not make a general question "about" that marker…
        body = self.ask([model("천곡황금박쥐동굴은 실내라 비 오는 날에도 좋아요.", sources=[CAVE])],
                        "비 오는 날 갈 만한 실내", vector_docs=[CAVE], focused=MUKHO)
        self.assertEqual(body["sources"], [CAVE])
        # …unless the question points at it.
        body = self.ask([model("천곡황금박쥐동굴이 좋아요.", sources=[CAVE])],
                        "여기 수영장 있어?", vector_docs=[CAVE], focused=MUKHO)
        self.assertEqual(body["reply"], service.NO_INFO)


class AttributedSourcesTests(_Case):
    """The 천곡동굴 opening-hours regression: a correct answer from a named document, sent
    without sources, was thrown away as "no documents"."""

    def test_an_uncited_answer_that_is_in_the_named_document_keeps_it_as_source(self):
        content = retrieval.load_content(self.settings.content_dir)
        fact = next(n for n in service._NUMBER.findall(content.docs[DONGHAE].body) if len(n) == 4)
        body = self.ask([model(f"동해역은 {fact}년에 문을 열었어요.")], "동해역 언제 생겼어?")
        self.assertEqual(body["sources"], [DONGHAE])
        self.assertIn(fact, body["reply"])

    def test_a_number_the_document_does_not_have_is_not_attributed(self):
        body = self.ask([model("동해역은 1888년에 문을 열었어요.")], "동해역 언제 생겼어?")
        self.assertEqual(body["reply"], service.NO_INFO)
        self.assertEqual(body["sources"], [])

    def test_a_document_found_only_by_similarity_is_never_attributed(self):
        body = self.ask([model("천곡황금박쥐동굴은 1991년에 발견됐어요.")], "오래된 곳 알려줘",
                        vector_docs=[CAVE])
        self.assertEqual(body["reply"], service.NO_INFO)

    def test_backing_needs_every_number_or_several_shared_words(self):
        body = "이용시간: 08:30~18:00, 입장 마감 17:30. 어른 5,000원"
        self.assertTrue(service._backed_by("입장 마감은 17:30이에요.", body))
        self.assertTrue(service._backed_by("어른은 5,000원이에요.", body))
        self.assertFalse(service._backed_by("입장 마감은 17:30이고 어른은 7,000원이에요.", body))
        self.assertFalse(service._backed_by("주말에는 사람이 많아요.", body))


class BasisSaidOnceTests(unittest.TestCase):
    basis = "최근 토요일 3주(11/14, 11/7, 10/31) 운행 기록 기준의 어림 시각이에요."

    def test_the_models_own_basis_wording_is_removed(self):
        cases = {
            "동해역은 15시대가 가장 붐벼요. (시각은 최근 토요일 운행 기록 기반 어림이에요)":
                "동해역은 15시대가 가장 붐벼요.",
            "마지막 열차는 18:34 출발이에요(최근 토요일 운행 기록 기준 어림이에요).":
                "마지막 열차는 18:34 출발이에요.",
            "최근 토요일 운행 기록 기준으로는 동해역이 63편이에요.": "동해역이 63편이에요.",
            "열차는 09:32에 있어요. 다만 최근 토요일 운행 기록 기준 어림 시각이라서 당일 확인이 필요해요.":
                "열차는 09:32에 있어요.",
        }
        for reply, expected in cases.items():
            with self.subTest(reply):
                self.assertEqual(service._without_own_basis(reply), expected)

    def test_times_and_everything_else_are_left_alone(self):
        for reply in ("동해역에는 09:32, 10:04에 열차가 있어요.", "기록적인 인파가 몰려요.",
                      "어림이에요."):  # a reply that is nothing else is not emptied
            with self.subTest(reply):
                self.assertEqual(service._without_own_basis(reply), reply)

    def test_the_served_answer_states_the_basis_exactly_once(self):
        settings = Settings(chat_mock=False, openai_api_key="test", llm_usage_log=os.devnull)
        client = TestClient(create_app(settings))
        tool = {"name": "station_rhythm", "station_id": DONGHAE, "place_id": None, "after": None}
        final = model("동해역은 15시대가 가장 붐벼요(최근 토요일 운행 기록 기준 어림이에요).",
                      "train_lookup")
        with patch.object(llm, "chat_json", side_effect=[
                model("확인해 볼게요.", "train_lookup", tool=tool), final]):
            body = client.post(URL, json={"message": "동해역 열차 언제 몰려?",
                                          "context": {"focused": DONGHAE}}).json()
        if "토요일 3주" in body["reply"]:  # snapshot timetable in the test database
            self.assertEqual(body["reply"].count("어림"), 1)
            self.assertEqual(body["reply"].count("운행 기록"), 1)
        self.assertTrue(body["reply"].startswith("동해역은 15시대가 가장 붐벼요."))


if __name__ == "__main__":
    unittest.main()
