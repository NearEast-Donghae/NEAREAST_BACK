"""Two server rules: walking time between two places is computed, never guessed, and a
question about the Hapyeong crossing or market days always carries its document as a source.
"""

import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from legacy_support import create_app

from neareast_api.core.config import Settings
from neareast_api.db.entities import PlaceEntity
from neareast_api.modules.chat import llm
from neareast_api.modules.places.service import Place
from neareast_api.modules.recommend import service as recommend

URL = "/api/v1/chat/messages"
DONGHAE, MUKHO, MANGSANG = "station_donghae", "station_mukho", "station_mangsang"
MARKET, CAVE = "poi_bukpyeong_market", "poi_cheongok_cave"
HAPYEONG = "safety_hapyeong_crossing"
NO_POSITION = "아직 위치 정보가 없어 도보 시간을 계산할 수 없어요"
QUESTION = "동해역에서 북평민속시장까지 걸어가면 얼마나 걸려?"
# made up: only the distances matter (0.003 degrees of latitude is 6 minutes on foot)
COORDINATES = {DONGHAE: (37.500, 129.10), MARKET: (37.503, 129.10), CAVE: (37.90, 129.10)}


def model(reply="좋아요.", intent="knowledge", sources=(), tool=None):
    return {"intent": intent, "reply": reply, "chips": ["다음"], "proposals": [],
            "sources": list(sources), "tool_request": tool}


class _Case(unittest.TestCase):
    settings = Settings()  # mock mode
    coordinates: dict = {}

    def setUp(self):
        self.app = create_app(self.settings)
        self.addCleanup(self.app.state.databaseEngine.dispose)
        with self.app.state.sessionFactory() as session:
            for place_id, (lat, lng) in self.coordinates.items():
                place = session.get(PlaceEntity, place_id)
                place.lat, place.lng = lat, lng
            session.commit()
        self.client = TestClient(self.app)

    def ask(self, message, replies=None, **context):
        """`replies`: what the (fake) model says, for the model path."""
        if replies is None:
            response = self.client.post(URL, json={"message": message, "context": context})
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()
        with patch.object(llm, "chat_json", side_effect=list(replies)) as chat:
            response = self.client.post(URL, json={"message": message, "context": context})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json(), chat


class WalkRuleTests(unittest.TestCase):
    def place(self, lat, lng):
        return Place("x", "x", "poi", lat=lat, lng=lng)

    def test_walk_between_says_why_there_is_no_time(self):
        near = recommend.walk_between(self.place(37.50, 129.1), self.place(37.51, 129.1))
        self.assertEqual((near.minutes, near.reason), (20, None))
        missing = recommend.walk_between(self.place(None, None), self.place(37.5, 129.1))
        self.assertEqual((missing.minutes, missing.reason), (None, "no_coordinates"))
        far = recommend.walk_between(self.place(37.50, 129.1), self.place(37.90, 129.1))
        self.assertEqual((far.minutes, far.reason), (None, "too_far"))


class WalkWithoutCoordinatesTests(_Case):
    """The seed has no coordinates yet: the answer says so instead of guessing."""

    def test_two_named_places(self):
        body = self.ask(QUESTION)
        self.assertEqual(body["tool_used"], "walk_time")
        self.assertIn(NO_POSITION, body["reply"])
        self.assertIn("동해역", body["reply"])
        self.assertIn("북평민속시장", body["reply"])
        self.assertNotIn("분", body["reply"].replace("분기", ""))  # no minutes are made up
        self.assertNotIn("예시 데이터", body["reply"])
        self.assertEqual(body["proposals"], [])

    def test_other_questions_are_not_walk_questions(self):
        for message, context in (
            ("동해역에서 묵호역까지 기차로 얼마나 걸려?", {}),
            ("북평민속시장 근처에 걸어갈 만한 데 있어?", {}),  # one place, nothing open
            ("걷는 거 좋아해?", {}),
            ("북평민속시장 근처에 걸어갈 만한 데 있어?", {"focused": DONGHAE}),  # asks for places
            ("동해역 열차 언제 와?", {}),
        ):
            with self.subTest(message):
                self.assertNotEqual(self.ask(message, **context)["tool_used"], "walk_time")


class WalkWithCoordinatesTests(_Case):
    coordinates = COORDINATES

    def test_time_is_the_straight_line_estimate_and_says_so(self):
        body = self.ask(QUESTION)
        self.assertEqual(body["tool_used"], "walk_time")
        self.assertIn("약 6분", body["reply"])
        self.assertIn("어림", body["reply"])
        self.assertNotIn(NO_POSITION, body["reply"])

    def test_here_means_the_open_marker(self):
        body = self.ask("여기서 북평민속시장까지 걸어서 얼마나 걸려?", focused=DONGHAE)
        self.assertEqual(body["tool_used"], "walk_time")
        self.assertIn("동해역에서 북평민속시장까지", body["reply"])
        self.assertIn("약 6분", body["reply"])

    def test_order_follows_the_question(self):
        body = self.ask("북평시장에서 동해역까지 도보로 얼마나 걸려?")
        self.assertIn("북평민속시장에서 동해역까지", body["reply"])

    def test_one_place_without_coordinates_is_still_no_answer(self):
        body = self.ask("동해역에서 묵호역까지 걸어가면 얼마나 걸려?")
        self.assertIn(NO_POSITION, body["reply"])

    def test_too_far_to_walk_is_said_plainly(self):
        body = self.ask("동해역에서 천곡동굴까지 걸어갈 수 있어?")
        self.assertEqual(body["tool_used"], "walk_time")
        self.assertIn("걸어가기에는 멀어요", body["reply"])
        self.assertNotIn("약", body["reply"])


class WalkModelPathTests(_Case):
    settings = Settings(chat_mock=False, openai_api_key="test", llm_usage_log=os.devnull)
    coordinates = COORDINATES

    def test_the_server_computes_it_even_when_the_model_does_not_ask_for_the_tool(self):
        (body, chat) = self.ask(QUESTION, [model("도보 시간은 아직 확인된 정보가 없어요.")])
        self.assertEqual((body["mode"], body["tool_used"]), ("llm", "walk_time"))
        self.assertIn("약 6분", body["reply"])
        self.assertEqual(chat.call_count, 1)  # the answer is written from the result

    def test_minutes_the_model_made_up_never_reach_the_user(self):
        body, _ = self.ask(QUESTION, [model("걸어서 25분쯤 걸려요.")])
        self.assertNotIn("25분", body["reply"])
        self.assertIn("약 6분", body["reply"])

    def test_the_model_can_ask_for_the_tool_in_its_own_words(self):
        tool = {"name": "walk_time", "station_id": DONGHAE, "place_id": MARKET, "after": None}
        body, chat = self.ask("거기까지 발로 가면 오래 걸릴까?", [model(tool=tool)], focused=DONGHAE)
        self.assertEqual(body["tool_used"], "walk_time")
        self.assertIn("약 6분", body["reply"])
        self.assertEqual(chat.call_count, 1)

    def test_a_tool_request_with_unknown_places_is_not_answered_with_a_number(self):
        tool = {"name": "walk_time", "station_id": "station_nowhere", "place_id": MARKET,
                "after": None}
        body, _ = self.ask("거기까지 발로 가면 오래 걸릴까?",
                           [model(tool=tool), model("아직 확인된 정보가 없어요.")])
        self.assertIsNone(body["tool_used"])
        self.assertNotIn("약", body["reply"])


class AskedDocumentIsASourceTests(_Case):
    def test_hapyeong_question_cites_the_hapyeong_document(self):
        body = self.ask("하평 건널목에서 기다리면서 찍어도 돼?")
        self.assertIn(HAPYEONG, body["sources"])
        self.assertNotIn(HAPYEONG, self.ask("건널목 안에서 사진 찍어도 돼?")["sources"])

    def test_market_day_question_cites_the_market_document(self):
        for message in ("북평민속시장 그날 열어?", "그날 장날이야?", "장이 서는 날이 언제야?"):
            with self.subTest(message):
                self.assertIn(MARKET, self.ask(message, date="2026-11-21")["sources"])
        self.assertNotIn(MARKET, self.ask("동해역 역사 알려줘", date="2026-11-21")["sources"])


class AskedDocumentModelPathTests(_Case):
    settings = Settings(chat_mock=False, openai_api_key="test", llm_usage_log=os.devnull)

    def prompt(self, chat):
        return "\n".join(m["content"] for m in chat.call_args.args[1])

    def test_the_model_forgetting_to_cite_does_not_lose_the_source(self):
        reply = "하평 건널목 안에서의 촬영은 권하지 않아요. 역 승강장처럼 공개된 장소가 좋아요."
        body, chat = self.ask("하평 건널목에서 기다리면서 찍어도 돼?",
                              [model(reply, intent="safety", sources=["safety_track_crossing"])])
        self.assertIn(HAPYEONG, body["sources"])
        self.assertIn(f"<{HAPYEONG}>", self.prompt(chat))  # and the model was given it

    def test_market_day_answer_keeps_its_source_and_is_not_replaced(self):
        body, chat = self.ask("그날 장이 서?", [model("그날은 장이 서지 않아요.")],
                              date="2026-11-21")
        self.assertIn(MARKET, body["sources"])
        self.assertIn(f"<{MARKET}>", self.prompt(chat))  # given even without a keyword match
        self.assertNotIn("확인된 정보가 없어요", body["reply"])
        self.assertIn("11월 23일", body["reply"])  # the server's own market-day sentence

    def test_unrelated_answers_get_no_extra_sources(self):
        body, _ = self.ask("동해역 역사 알려줘",
                           [model("동해역은 분기역이에요.", sources=[DONGHAE])], focused=DONGHAE)
        self.assertEqual(body["sources"], [DONGHAE])


if __name__ == "__main__":
    unittest.main()
