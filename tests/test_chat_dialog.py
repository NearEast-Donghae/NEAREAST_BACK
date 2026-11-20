"""Bugs found by using Nearie on the real screen (2026-11-20). Each rule is checked both
ways: when it must apply and when it must not.

The conversation that showed them is kept as the "real conversation" scenarios in
tests/chat_scenarios/scenarios.json (ids starting with "rc-").
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
DONGHAE, MUKHO, CHUAM, MANGSANG = (
    "station_donghae", "station_mukho", "station_chuam", "station_mangsang")
TOMORROW = (dt.datetime.now(service.KST).date() + dt.timedelta(days=1)).isoformat()


def user(text):
    return {"role": "user", "content": text}


def nearie(text):
    return {"role": "assistant", "content": text}


def model(reply="좋아요.", intent="knowledge", sources=(), plan=None, proposals=()):
    return {"intent": intent, "reply": reply, "chips": ["다음"], "proposals": list(proposals),
            "sources": list(sources), "tool_request": None, "plan": plan}


class _Case(unittest.TestCase):
    def setUp(self):
        self.mock = TestClient(create_app(Settings()))
        self.real = TestClient(create_app(
            Settings(chat_mock=False, openai_api_key="test", llm_usage_log=os.devnull)))

    def ask(self, message, history=(), **context):
        response = self.mock.post(
            URL, json={"message": message, "context": context, "history": list(history)})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def ask_model(self, drafts, message, history=(), **context):
        with patch.object(llm, "chat_json", side_effect=list(drafts)) as chat:
            response = self.real.post(
                URL, json={"message": message, "context": context, "history": list(history)})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json(), chat


class PlacesToVisitTests(_Case):
    """ "망상역, 동해역, 추암역 볼 테니까 계획 짜줘": the places named are places to visit."""

    def test_places_named_in_the_request_are_required(self):
        body = self.ask(f"{TOMORROW}에 망상역, 동해역, 추암역 볼테니까 계획 짜줘봐")
        payload = body["action"]["payload"]
        self.assertEqual(payload["requiredPlaceIds"], [MANGSANG, DONGHAE, CHUAM])
        self.assertIsNone(payload["departurePlaceId"])  # none of them was called a departure
        self.assertIn("망상역, 동해역, 추암역", body["reply"])

    def test_named_places_are_enough_to_start_no_pins_needed(self):
        body = self.ask("망상역, 동해역, 추암역 볼테니까 계획 짜줘봐")
        self.assertTrue(body["reply"].startswith("어느 날짜로"))  # asks the day, not the places
        self.assertNotIn("어디를 방문", body["reply"])

    def test_no_place_anywhere_still_asks_where(self):
        body = self.ask("계획 짜줘")
        self.assertIn("어디를 방문", body["reply"])
        self.assertIsNone(body["action"])

    def test_model_guess_of_a_departure_does_not_take_a_visit_away(self):
        plan = {"date": None, "start_place": DONGHAE, "start_time": None, "end_place": None,
                "end_time": None}
        body, _ = self.ask_model(
            [model("넘겨 드릴게요.", "plan_request", plan=plan)],
            f"{TOMORROW}에 동해역이랑 추암역 볼 거야. 일정 짜줘")
        payload = body["action"]["payload"]
        # The user did name 동해역 (so the model's value passes the check) but as a place to
        # see. Either way it is in the plan exactly once.
        places = [payload["departurePlaceId"], *payload["requiredPlaceIds"]]
        self.assertEqual(sorted(p for p in places if p), [CHUAM, DONGHAE])

    def test_departure_said_as_such_is_a_departure_not_a_visit(self):
        plan = {"date": None, "start_place": DONGHAE, "start_time": "09:00", "end_place": None,
                "end_time": None}
        body, _ = self.ask_model(
            [model("넘겨 드릴게요.", "plan_request", plan=plan)],
            f"{TOMORROW} 9시에 동해역에서 출발해서 추암역 보는 일정 짜줘")
        payload = body["action"]["payload"]
        self.assertEqual(payload["departurePlaceId"], DONGHAE)
        self.assertEqual(payload["requiredPlaceIds"], [CHUAM])
        self.assertEqual(payload["startTime"], "09:00")


class EarlierTurnsTests(_Case):
    """Places, the day and the hours said earlier in the conversation reach the conditions."""

    history = [user("흠 그럼 망상역, 동해역, 추암역 볼테니까 계획 짜줘봐"),
               nearie(service.PLAN_NEEDS_DATE),
               user("동해역 열차 리듬"), nearie("동해역에는 하루 63편이 서요."),
               user("계획 짜줘 대충"), nearie(service.PLAN_NEEDS_DATE)]

    def test_places_from_the_earlier_request(self):
        body = self.ask("내일로 일정 짜줘", self.history)
        payload = body["action"]["payload"]
        self.assertEqual(payload["travelDate"], TOMORROW)
        self.assertEqual(payload["requiredPlaceIds"], [MANGSANG, DONGHAE, CHUAM])

    def test_day_and_hours_from_earlier_turns(self):
        history = [user("내일 9시부터 오후 5시까지 묵호역 보러 갈 거야"), nearie("좋아요.")]
        payload = self.ask("일정 짜줘", history)["action"]["payload"]
        self.assertEqual(payload["travelDate"], TOMORROW)
        self.assertEqual((payload["startTime"], payload["endTime"]), ("09:00", "17:00"))
        self.assertEqual(payload["requiredPlaceIds"], [MUKHO])

    def test_a_place_only_asked_about_is_not_a_visit(self):
        history = [user("묵호역은 언제 문을 열었어?"), nearie("1961년이에요."),
                   user("내일 동해역 볼 거야"), nearie("좋아요.")]
        payload = self.ask("일정 짜줘", history)["action"]["payload"]
        self.assertEqual(payload["requiredPlaceIds"], [DONGHAE])

    def test_the_day_on_the_screen_beats_an_earlier_turn(self):
        history = [user("내일 동해역 볼 거야"), nearie("좋아요.")]
        payload = self.ask("일정 짜줘", history, date="2026-12-05")["action"]["payload"]
        self.assertEqual(payload["travelDate"], "2026-12-05")

    def test_spoken_hours(self):
        cases = {
            "9시부터 5시까지": ("09:00", "17:00"),
            "오전 10시 반에 출발해서 저녁 7시까지": ("10:30", "19:00"),
            "오후 1시부터": ("13:00", None),
            "3시부터 볼래": (None, None),  # 03:00 or 15:00: not guessed
            "열차는 9시에 와?": (None, None),  # a question about a train, not the day's hours
        }
        for text, expected in cases.items():
            with self.subTest(text):
                self.assertEqual(service.spoken_times(text), expected)


class AgreementTests(_Case):
    """ "ㅇㅇ", "응", "그래", "좋아" answer what was just said."""

    card = "11월 21일 여행 조건을 정리했어요. 방문할 곳은 1곳(동해역)이에요."

    def test_words_that_are_a_yes(self):
        for message in ("ㅇㅇ", "응", "그래", "좋아", "네", "ㅇㅋ", "좋아요!", "그렇게 해줘", "OK"):
            with self.subTest(message):
                self.assertTrue(service.is_agreement(message))

    def test_words_that_are_not(self):
        for message in ("응 근데 묵호역은?", "좋아하는 열차가 뭐야?", "그래서 몇 시야?", "네이버",
                        "동해역", "어디", "ㅇㅇ 근데 추암역도 넣어줘"):
            with self.subTest(message):
                self.assertFalse(service.is_agreement(message))

    def test_yes_after_a_plan_card_continues_to_the_plan_screen(self):
        history = [user(f"{TOMORROW}에 동해역 볼 거야. 일정 짜줘"), nearie(self.card)]
        for ask in (self.ask, lambda m, h: self.ask_model([], m, h)[0]):
            body = ask("ㅇㅇ", history)
            self.assertEqual(body["intent"], "plan_request")
            self.assertIn("일정 화면", body["reply"])
            self.assertNotIn("확인된 정보", body["reply"])
            self.assertEqual(body["action"]["payload"]["requiredPlaceIds"], [DONGHAE])

    def test_yes_after_a_plan_card_needs_no_model_call(self):
        history = [user(f"{TOMORROW}에 동해역 볼 거야. 일정 짜줘"), nearie(self.card)]
        _, chat = self.ask_model([], "응", history)
        chat.assert_not_called()

    def test_yes_to_which_day_asks_again_with_day_chips(self):
        history = [user("동해역 볼 거야. 일정 짜줘"), nearie(service.PLAN_NEEDS_DATE)]
        body = self.ask("ㅇㅇ", history)
        self.assertIsNone(body["action"])
        self.assertNotEqual(body["reply"], service.PLAN_NEEDS_DATE)  # not the same line again
        self.assertTrue(any("일정 짜줘" in chip for chip in body["chips"]))

    def test_yes_elsewhere_is_never_no_information(self):
        history = [user("동해역 알려줘"), nearie("동해역은 분기역이에요.")]
        self.assertEqual(self.ask("ㅇㅇ", history)["reply"], service.ACK_REPLY)
        body, _ = self.ask_model([model("아직 확인된 정보가 없어요.")], "ㅇㅇ", history)
        self.assertEqual(body["reply"], service.ACK_REPLY)
        self.assertEqual(body["intent"], "smalltalk")

    def test_yes_to_a_proposal_does_not_add_it_for_the_user(self):
        history = [user("동해역 근처"), nearie("동해역 근처에 북평민속시장이 있어요. 담을지는 직접 골라 주세요.")]
        body = self.ask("좋아", history)
        self.assertEqual(body["reply"], service.PROPOSAL_AGREED)
        self.assertEqual(body["proposals"], [])

    def test_the_models_own_answer_to_a_yes_is_kept(self):
        history = [user("동해역 알려줘"), nearie("동해역은 분기역이에요. 열차 리듬도 볼까요?")]
        body, _ = self.ask_model([model("좋아요, 동해역 열차 리듬을 보려면 아래 칩을 눌러 주세요.",
                                        "smalltalk")], "응", history)
        self.assertIn("칩을 눌러", body["reply"])


class NoInformationTests(_Case):
    """The "open it on the map" hint only when "here/this" has nothing to point at."""

    def test_a_question_that_is_not_about_a_place_gets_no_map_hint(self):
        for body in (self.ask("완목 신호기는 없어?"),
                     self.ask_model([model("완목 신호기는 동해역에 있어요.")], "완목 신호기는 없어?")[0]):
            self.assertEqual(body["reply"], service.NO_INFO)
            self.assertNotIn("지도에서", body["reply"])

    def test_here_with_nothing_open_gets_the_hint(self):
        body, _ = self.ask_model([model("여기는 멋진 곳이에요.")], "여기 뭐가 유명해?")
        self.assertEqual(body["reply"], f"{service.NO_INFO} {service.NO_INFO_HINT}")

    def test_here_with_a_place_open_does_not(self):
        body, _ = self.ask_model([model("여기는 멋진 곳이에요.")], "여기 완목 신호기 있어?",
                                 focused=DONGHAE)
        self.assertEqual(body["reply"], service.NO_INFO)


class SafetyTests(_Case):
    """One line that draws the limit; the rest of the answer survives."""

    def test_asking_to_go_inside_or_not_saying_where(self):
        for message in ("건널목 안에서 사진 찍어도 돼?", "선로 옆에 서서 찍을 만한 데 있어?",
                        "하평 건널목에서 기다리면서 찍어도 돼?", "철길 따라 걸으면서 찍고 싶어",
                        "건널목 근처 선로 안에서 찍고 싶어", "차단기"):
            with self.subTest(message):
                self.assertTrue(service.asks_entry(message))

    def test_asking_about_a_public_place_by_the_crossing(self):
        for message in ("건널목 도로는 없어? 거기서 사진 찍구 싶음",
                        "추암역이랑 뭐 주변에 건널목 하나 볼까 하는데 뭐 없나?",
                        "건널목 근처 인도에서 봐도 돼?", "차단기 밖에서 구경해도 돼?"):
            with self.subTest(message):
                self.assertFalse(service.asks_entry(message))

    def test_the_models_alternatives_survive_the_refusal_line(self):
        reply = "추암역 승강장에서는 바다와 열차를 함께 볼 수 있어요. 촛대바위 쪽 산책로도 좋아요."
        body, _ = self.ask_model([model(reply, "safety")], "건널목 안에서 찍고 싶어")
        self.assertEqual(body["reply"], f"{service.SAFETY_NO} {reply}")

    def test_a_sentence_that_sends_the_user_inside_is_removed(self):
        reply = "차단기 안쪽으로 들어가면 잘 보여요. 승강장에서도 볼 수 있어요."
        body, _ = self.ask_model([model(reply, "safety")], "건널목 안에서 찍고 싶어")
        self.assertNotIn("들어가면 잘 보여요", body["reply"])
        self.assertIn("승강장에서도 볼 수 있어요", body["reply"])
        self.assertIn("권하지 않아요", body["reply"])

    def test_a_public_road_is_not_refused(self):
        reply = "하평 건널목은 안전요원이 있는 곳이에요."
        for body in (self.ask("건널목 도로는 없어? 거기서 사진 찍구 싶음"),
                     self.ask_model([model(reply, "safety", ["safety_hapyeong_crossing"])],
                                    "건널목 도로는 없어? 거기서 사진 찍구 싶음")[0]):
            self.assertNotIn("권하지 않아요", body["reply"])
            self.assertIn("차단기 밖", body["reply"])
            self.assertIn("차단기 안쪽", body["reply"])  # the limit is still stated
            self.assertIn("safety_track_crossing", body["sources"])
        self.assertIn(reply, body["reply"])

    def test_the_same_safety_line_is_not_said_twice(self):
        first = self.ask("추암역 주변에 건널목 하나 볼까 하는데 뭐 없나?")
        self.assertIn(service.SAFETY_PUBLIC, first["reply"])
        history = [user("추암역 주변에 건널목 하나 볼까 하는데 뭐 없나?"), nearie(first["reply"])]
        second = self.ask("건널목 도로는 없어? 거기서 사진 찍구 싶음", history)
        self.assertNotIn(service.SAFETY_PUBLIC, second["reply"])
        self.assertIn("차단기 밖", second["reply"])

        inside = self.ask("건널목 안에서 찍어도 돼?")
        again = self.ask("차단기 안쪽에서 딱 한 장만", [user("건널목 안에서 찍어도 돼?"),
                                                 nearie(inside["reply"])])
        self.assertNotIn(service.SAFETY_NO, again["reply"])
        self.assertIn("권하지 않아요", again["reply"])  # still a clear no

    def test_the_alternative_names_the_station_in_play(self):
        body, _ = self.ask_model([model("위험해요.", "safety")], "건널목 안에서 찍고 싶어",
                                 focused=MUKHO)
        self.assertIn("묵호역 승강장", body["reply"])
        body, _ = self.ask_model([model("위험해요.", "safety")], "건널목 안에서 찍고 싶어",
                                 focused=MANGSANG)  # no passengers there: not offered
        self.assertNotIn("망상역 승강장", body["reply"])
        self.assertIn("공개된 장소", body["reply"])


class RealModelSafetyTests(_Case):
    """What gpt-5.4-nano actually answered when the 2026-11-20 conversation was replayed."""

    refusal = ("건널목은 선로·차단기 안쪽이나 건널목 안에서 보거나 촬영하는 건 권하지 않아요. "
               "대신 역 맞이방이나 승강장처럼 공개된 곳에서 열차를 함께 볼 수 있어요.")

    def test_a_refusal_to_a_public_place_question_is_not_served(self):
        body, _ = self.ask_model([model(self.refusal, "safety")],
                                 "추암역이랑 뭐 주변에 건널목 하나 볼까 하는데 뭐 없나?")
        self.assertNotIn("권하지 않아요", body["reply"])
        self.assertTrue(body["reply"].startswith(service.SAFETY_PUBLIC))
        self.assertIn("역 맞이방이나 승강장처럼 공개된 곳", body["reply"])  # the rest is kept
        self.assertNotIn("요. 대신 역", body["reply"])  # no dangling "대신"

    def test_the_models_own_public_road_answer_needs_no_extra_line(self):
        reply = ("차단기 안쪽이나 선로 부지에서는 촬영은 권하지 않아요. 대신 차단기 밖의 공개된 도로나 "
                 "인도 쪽에서라면 사진을 계획해볼 수 있어요.")
        body, _ = self.ask_model([model(reply, "safety")], "건널목 도로는 없어? 거기서 사진 찍구 싶음")
        self.assertEqual(body["reply"], "차단기 밖의 공개된 도로나 인도 쪽에서라면 사진을 계획해볼 수 있어요.")

    def test_a_follow_up_that_is_not_about_the_tracks_does_not_repeat_the_refusal(self):
        history = [user("건널목 안에서 찍어도 돼?"), nearie(self.refusal)]
        reply = ("추암역 쪽 건널목 안에서 보는 건 권하지 않아요. 대신 역 승강장처럼 공개된 장소에서 "
                 "열차를 관찰하는 쪽으로 잡아 볼게요.")
        body, _ = self.ask_model([model(reply, "safety")], "안전하게 볼 수 있는 곳", history)
        self.assertEqual(body["reply"], "역 승강장처럼 공개된 장소에서 열차를 관찰하는 쪽으로 잡아 볼게요.")

    def test_a_first_refusal_and_a_refusal_with_nothing_else_are_kept(self):
        reply = "선로 가까이에서 보는 건 권하지 않아요. 승강장에서 보세요."
        body, _ = self.ask_model([model(reply, "safety")], "안전하게 볼 수 있는 곳")
        self.assertEqual(body["reply"], reply)  # nothing said earlier: not a repeat
        only = "그건 권하지 않아요."
        body, _ = self.ask_model([model(only, "safety")], "안전하게 볼 수 있는 곳",
                                 [user("건널목 안에서 찍어도 돼?"), nearie(self.refusal)])
        self.assertEqual(body["reply"], only)  # never an empty answer


class WordingTests(_Case):
    def test_the_plan_summary_reads_as_sentences(self):
        body = self.ask("일정 짜줘", date="2026-11-21", pins=[DONGHAE, MUKHO])
        self.assertNotIn("곳(", body["reply"].split("방문할 곳은")[0])
        self.assertIn("방문할 곳은 2곳(동해역, 묵호역)이에요.", body["reply"])
        self.assertNotIn("을 확인하고", body["reply"])

    def test_a_station_without_passengers_is_marked_observe_only(self):
        body = self.ask("망상역 볼 거야. 일정 짜줘", date="2026-11-21")
        self.assertIn("관찰만", body["reply"])
        self.assertNotIn("관찰만", self.ask("동해역 볼 거야. 일정 짜줘", date="2026-11-21")["reply"])


if __name__ == "__main__":
    unittest.main()
