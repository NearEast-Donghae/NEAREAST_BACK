"""Nearie hands a "plan my day" request over to the plan screen; it never plans in chat.

The reply for such a request is written by the server, so no responder (mock or LLM) can
slip an itinerary into the conversation. Written before the code.
"""

import datetime as dt
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from legacy_support import create_app
from legacy_support import setUpModule as setUpModule
from legacy_support import tearDownModule as tearDownModule

from neareast_api.core.config import Settings
from neareast_api.modules.chat import llm, service

URL = "/api/v1/chat/messages"
DONGHAE, MUKHO, MANGSANG = "station_donghae", "station_mukho", "station_mangsang"
MARKET = "poi_bukpyeong_market"
PINS = [DONGHAE, MUKHO, MARKET]
TODAY = dt.date(2026, 11, 19)


class DetectionTests(unittest.TestCase):
    def test_requests_to_make_a_plan(self):
        for message in ("일정 짜줘", "계획 세워줘", "이대로 하루 만들어줘", "하루 일정 좀 짜 주세요",
                        "내 하루 일정 다 짜서 넣어줘", "토요일 코스 잡아줘", "11월 21일로 일정 짜줘",
                        "동해역 핀 빼고 일정 다시 짜줘", "계획 좀 세워 줄래?", "동선 짜줄래"):
            with self.subTest(message):
                self.assertTrue(service.is_plan_request(message))

    def test_questions_are_not_requests(self):
        for message in ("일정 화면은 뭐 하는 곳이야?", "계획 짜는 팁 있어?", "동해역 열차 언제 와?",
                        "동해역 일정에 넣을 만한 곳 추천해줘", "하루에 열차 몇 편 다녀?",
                        "여기 뭐야?", "일정은 내가 짤게", "안녕"):
            with self.subTest(message):
                self.assertFalse(service.is_plan_request(message))

    def test_date_said_in_the_message(self):
        cases = {
            "2026-11-21로 일정 짜줘": dt.date(2026, 11, 21),
            "11월 21일로 일정 짜줘": dt.date(2026, 11, 21),
            "1월 3일 일정 짜줘": dt.date(2027, 1, 3),  # already past this year: next year
            "오늘로 일정 짜줘": TODAY,
            "내일로 일정 짜줘": dt.date(2026, 11, 20),
            "일정 짜줘": None,
            "13월 40일로 일정 짜줘": None,
        }
        for message, expected in cases.items():
            with self.subTest(message):
                self.assertEqual(service.date_in_message(message, TODAY), expected)


class _Case(unittest.TestCase):
    settings = Settings()

    def setUp(self):
        self.client = TestClient(create_app(self.settings))

    def ask(self, message, history=(), **context):
        response = self.client.post(
            URL, json={"message": message, "context": context, "history": list(history)})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()


class HandoffTests(_Case):
    def test_action_carries_the_screen_context_as_planning_conditions(self):
        body = self.ask("일정 짜줘", date="2026-11-21", pins=PINS, depth="full",
                        dismissed=["poi_nongoldam"])
        self.assertEqual(body["intent"], "plan_request")
        self.assertEqual(body["action"], {"type": "plan_request", "payload": {
            "travelDate": "2026-11-21", "endDate": None, "startTime": None, "endTime": None,
            "departurePlaceId": None, "arrivalPlaceId": None,
            "requiredPlaceIds": [DONGHAE, MUKHO, MARKET],
            "preferredTrainTypes": [], "selectedTrainIds": [], "selectedTrains": [],
            "interests": [], "maxWalkingMinutes": None}})

    def test_the_payload_is_what_planning_requests_accept(self):
        from neareast_api.modules.itineraries.dto import PlanningConditionsDto
        payload = self.ask("일정 짜줘", date="2026-11-21", pins=PINS)["action"]["payload"]
        self.assertEqual(PlanningConditionsDto.model_validate(payload).requiredPlaceIds, PINS)

    def test_nearie_only_points_to_the_plan_screen(self):
        body = self.ask("일정 짜줘", date="2026-11-21", pins=PINS)
        self.assertIn("일정 화면", body["reply"])
        self.assertEqual(service._clock_claims(body["reply"]), [])
        self.assertEqual((body["proposals"], body["sources"], body["tool_used"]), ([], [], None))
        self.assertTrue(1 <= len(body["chips"]) <= 3)

    def test_other_answers_have_no_action(self):
        for message in ("동해역 열차 언제 와?", "여기 뭐야?", "안녕", "근처 추천해줘"):
            with self.subTest(message):
                body = self.ask(message, date="2026-11-21", pins=PINS, focused=DONGHAE)
                self.assertIsNone(body["action"])
                self.assertNotEqual(body["intent"], "plan_request")

    def test_a_date_said_in_the_message_wins_over_the_screen(self):
        body = self.ask("2026-11-28로 일정 짜줘", date="2026-11-21", pins=PINS)
        self.assertEqual(body["action"]["payload"]["travelDate"], "2026-11-28")

    def test_unknown_and_focused_places_are_not_required(self):
        body = self.ask("일정 짜줘", date="2026-11-21", pins=[DONGHAE, "poi_gone"], focused=MUKHO)
        self.assertEqual(body["action"]["payload"]["requiredPlaceIds"], [DONGHAE])

    def test_safety_still_comes_first(self):
        body = self.ask("건널목 안에서 찍는 일정 짜줘", date="2026-11-21", pins=PINS)
        self.assertEqual(body["intent"], "safety")
        self.assertIsNone(body["action"])
        self.assertIn("권하지 않아요", body["reply"])


class MissingConditionTests(_Case):
    def test_no_pins_asks_to_pin_first_and_nothing_else(self):
        body = self.ask("일정 짜줘")
        self.assertEqual((body["intent"], body["action"]), ("plan_request", None))
        self.assertIn("담아", body["reply"])
        self.assertNotIn("날짜", body["reply"])
        self.assertEqual(body["reply"].count("?"), 1)

    def test_no_date_asks_for_the_date_with_chips_that_answer_it(self):
        body = self.ask("일정 짜줘", pins=PINS)
        self.assertEqual((body["intent"], body["action"]), ("plan_request", None))
        self.assertIn("날짜", body["reply"])
        self.assertEqual(body["reply"].count("?"), 1)
        self.assertTrue(body["chips"])
        for chip in body["chips"]:  # every chip is a complete answer: tapping it hands over
            with self.subTest(chip):
                again = self.ask(chip, pins=PINS, history=[
                    {"role": "user", "content": "일정 짜줘"},
                    {"role": "assistant", "content": body["reply"]}])
                self.assertEqual(again["action"]["type"], "plan_request")
                self.assertEqual(again["action"]["payload"]["requiredPlaceIds"], PINS)


def model(intent="plan_request", reply="좋아요.", plan=None, tool=None, proposals=()):
    return {"intent": intent, "reply": reply, "chips": ["다음"], "proposals": list(proposals),
            "sources": [], "tool_request": tool, "plan": plan}


def plan(**fields):
    return {"date": None, "start_place": None, "start_time": None, "end_place": None,
            "end_time": None, **fields}


class ModelPathTests(_Case):
    settings = Settings(chat_mock=False, openai_api_key="test", llm_usage_log=os.devnull)

    def ask_model(self, replies, message, **context):
        with patch.object(llm, "chat_json", side_effect=list(replies)) as chat:
            body = self.ask(message, **context)
        return body, chat

    def test_spoken_places_allow_handoff_without_saved_places(self):
        said = plan(start_place=DONGHAE, start_time="09:00", end_place=MUKHO, end_time="18:00")
        body, _ = self.ask_model([model(plan=said)],
            "2026-11-21 아침 9시 동해역에서 출발해서 오후 6시 묵호역에 도착하는 일정 짜줘")
        payload = body["action"]["payload"]
        self.assertEqual(payload["departurePlaceId"], DONGHAE)
        self.assertEqual(payload["arrivalPlaceId"], MUKHO)
        self.assertEqual(payload["travelDate"], "2026-11-21")
        self.assertEqual(payload["requiredPlaceIds"], [])

    def test_guessed_places_do_not_bypass_missing_place_question(self):
        said = plan(start_place=DONGHAE, end_place=MUKHO)
        body, _ = self.ask_model([model(plan=said)], "2026-11-21 일정 짜줘")
        self.assertIsNone(body["action"])
        self.assertIn("어느 역", body["reply"])

    def test_spoken_places_without_date_ask_for_date(self):
        said = plan(start_place=DONGHAE, end_place=MUKHO)
        body, _ = self.ask_model([model(plan=said)], "동해역 출발 묵호역 도착 일정 짜줘")
        self.assertIsNone(body["action"])
        self.assertIn("날짜", body["reply"])

    def test_an_itinerary_written_by_the_model_never_reaches_the_user(self):
        made_up = "09:13 동해역 출발, 10:02 묵호역 도착, 12:00 북평민속시장 점심으로 짰어요."
        add = {"type": "add", "target": "poi_nongoldam", "reason": "코스에 넣었어요", "time_bound": False}
        body, _ = self.ask_model([model(reply=made_up, proposals=[add])], "일정 짜줘",
                                 date="2026-11-21", pins=PINS)
        self.assertEqual(body["mode"], "llm")
        self.assertNotIn("09:13", body["reply"])
        self.assertNotIn("짰어요", body["reply"])
        self.assertIn("일정 화면", body["reply"])
        self.assertEqual(body["proposals"], [])
        self.assertEqual(body["action"]["type"], "plan_request")

    def test_the_request_is_caught_even_if_the_model_calls_it_something_else(self):
        body, _ = self.ask_model([model(intent="proposal", reply="이렇게 다녀오세요.")], "일정 짜줘",
                                 date="2026-11-21", pins=PINS)
        self.assertEqual(body["intent"], "plan_request")
        self.assertIsNotNone(body["action"])

    def test_the_model_can_catch_wording_the_server_does_not_know(self):
        body, _ = self.ask_model([model()], "담아 둔 걸로 토요일을 어떻게 보낼지 정리해줘",
                                 date="2026-11-21", pins=PINS)
        self.assertEqual(body["intent"], "plan_request")
        self.assertIsNotNone(body["action"])

    def test_conditions_the_user_said_are_taken_from_the_model_after_checking(self):
        said = plan(start_place=DONGHAE, start_time="09:00", end_place=MUKHO, end_time="19:00")
        body, _ = self.ask_model([model(plan=said)], "9시에 동해역에서 나가 7시에 묵호역에 닿는 일정 짜줘",
                                 date="2026-11-21", pins=PINS)
        payload = body["action"]["payload"]
        self.assertEqual((payload["departurePlaceId"], payload["startTime"]), (DONGHAE, "09:00"))
        self.assertEqual((payload["arrivalPlaceId"], payload["endTime"]), (MUKHO, "19:00"))

    def test_conditions_the_model_made_up_are_dropped(self):
        said = plan(date="2026-13-45", start_place="station_nowhere", start_time="9시",
                    end_place=None, end_time="25:00")
        body, _ = self.ask_model([model(plan=said)], "일정 짜줘", date="2026-11-21", pins=PINS)
        payload = body["action"]["payload"]
        self.assertEqual(
            [payload[key] for key in ("travelDate", "departurePlaceId", "startTime", "endTime")],
            ["2026-11-21", None, None, None])

    def test_conditions_the_user_never_said_are_dropped(self):
        # Seen on the real model: the first saved station came back as the departure.
        guessed = plan(start_place=MUKHO, start_time="09:00", end_place=DONGHAE, end_time="18:00")
        body, _ = self.ask_model([model(plan=guessed)], "이대로 하루 일정 짜줘",
                                 date="2026-11-21", pins=PINS)
        payload = body["action"]["payload"]
        self.assertEqual(
            [payload[k] for k in ("departurePlaceId", "arrivalPlaceId", "startTime", "endTime")],
            [None, None, None, None])
        # A place only asked about earlier is not a departure ("묵호" inside 묵호등대).
        asked = [{"role": "user", "content": "묵호등대는 언제 처음 불을 밝혔어?"},
                 {"role": "assistant", "content": "1963년이에요."},
                 {"role": "user", "content": "동해역 열차 9시쯤 있어?"},
                 {"role": "assistant", "content": "확인해 볼게요."}]
        with patch.object(llm, "chat_json", return_value=model(plan=guessed)):
            body = self.ask("이대로 하루 일정 짜줘", history=asked, date="2026-11-21", pins=PINS)
        payload = body["action"]["payload"]
        self.assertEqual(
            [payload[k] for k in ("departurePlaceId", "arrivalPlaceId", "startTime", "endTime")],
            [None, None, None, None])
        # Said in an earlier turn of the conversation: taken.
        earlier = [{"role": "user", "content": "아침 9시에 묵호역에서 출발할 거야"},
                   {"role": "assistant", "content": "좋아요."}]
        with patch.object(llm, "chat_json", return_value=model(plan=guessed)):
            body = self.ask("이대로 하루 일정 짜줘", history=earlier, date="2026-11-21", pins=PINS)
        payload = body["action"]["payload"]
        self.assertEqual((payload["departurePlaceId"], payload["startTime"]), (MUKHO, "09:00"))
        self.assertEqual((payload["arrivalPlaceId"], payload["endTime"]), (None, None))

    def test_a_date_from_the_model_needs_the_user_to_have_spoken_of_a_day(self):
        body, _ = self.ask_model([model(plan=plan(date="2026-11-21"))], "일정 짜줘", pins=PINS)
        self.assertIsNone(body["action"])  # nobody said a day: Nearie asks for it
        self.assertIn("날짜", body["reply"])

    def test_an_end_before_the_start_is_left_for_the_plan_screen(self):
        said = plan(start_time="19:00", end_time="09:00")
        body, _ = self.ask_model([model(plan=said)], "저녁 7시부터 아침 9시까지 일정 짜줘",
                                 date="2026-11-21", pins=PINS)
        payload = body["action"]["payload"]
        self.assertEqual((payload["startTime"], payload["endTime"]), ("19:00", None))

    def test_the_model_can_supply_a_date_said_earlier_in_the_conversation(self):
        earlier = [{"role": "user", "content": "이번 주 토요일에 갈 거야"},
                   {"role": "assistant", "content": "좋아요."}]
        with patch.object(llm, "chat_json", return_value=model(plan=plan(date="2026-11-21"))):
            body = self.ask("일정 짜줘", history=earlier, pins=PINS)
        self.assertEqual(body["action"]["payload"]["travelDate"], "2026-11-21")

    def test_no_tool_call_is_spent_on_a_plan_request(self):
        tool = {"name": "station_rhythm", "station_id": DONGHAE, "place_id": None, "after": None}
        body, chat = self.ask_model([model(tool=tool)], "일정 짜줘", date="2026-11-21", pins=PINS)
        self.assertEqual(chat.call_count, 1)
        self.assertIsNone(body["tool_used"])

    def test_keeping_the_planning_is_not_a_request_even_if_the_model_says_so(self):
        for message in ("일정은 내가 직접 짤게", "계획은 제가 알아서 세울게요", "코스는 혼자 짜 볼게"):
            with self.subTest(message):
                body, _ = self.ask_model([model(reply="일정 화면으로 넘겨 드릴게요.")], message,
                                         date="2026-11-21", pins=PINS)
                self.assertIsNone(body["action"])
                self.assertNotEqual(body["intent"], "plan_request")
                self.assertNotIn("넘겨", body["reply"])
        body, _ = self.ask_model([model()], "내가 담은 걸로 일정 짜줘", date="2026-11-21", pins=PINS)
        self.assertIsNotNone(body["action"])  # "내가 담은" is not keeping the planning

    def test_asking_how_to_plan_is_not_a_request_even_if_the_model_says_so(self):
        # Seen on the real model: "계획 짜는 팁 있어?" came back as a hand-over.
        for message in ("계획 짜는 팁 있어?", "일정 세우는 방법 알려줘", "코스 짜는 요령 좀 알려줘"):
            with self.subTest(message):
                body, _ = self.ask_model([model(reply="일정 화면으로 넘겨 드릴게요.")], message,
                                         date="2026-11-21", pins=PINS)
                self.assertIsNone(body["action"])
                self.assertNotEqual(body["intent"], "plan_request")
                self.assertIn("일정 화면에서 직접", body["reply"])
        body, _ = self.ask_model([model()], "팁은 됐고 그냥 일정 짜줘", date="2026-11-21", pins=PINS)
        self.assertIsNotNone(body["action"])  # a request stays a request

    def test_a_model_plan_on_an_ordinary_answer_is_ignored(self):
        body, _ = self.ask_model(
            [model(intent="smalltalk", reply="안녕하세요.", plan=plan(date="2026-11-21"))],
            "안녕", date="2026-11-21", pins=PINS)
        self.assertIsNone(body["action"])


if __name__ == "__main__":
    unittest.main()
