"""Bounded agent: several read-only lookups before one answer, behind a setting.

The model is replaced by scripted outputs, so these check the loop and the server rules
around it, not the model's judgement (that is what scripts/eval_chat.py measures).
"""

import json
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from legacy_support import create_app
from legacy_support import setUpModule as setUpModule
from legacy_support import tearDownModule as tearDownModule

from neareast_api.core.config import Settings
from neareast_api.modules.chat import llm, prompts, service

URL = "/api/v1/chat/messages"
DONGHAE, MUKHO, CHUAM, MANGSANG = (
    "station_donghae", "station_mukho", "station_chuam", "station_mangsang")


def tool(name, station_id=None, place_id=None, after=None, query=None, category=None,
         before=None):
    return {"name": name, "station_id": station_id, "place_id": place_id, "after": after,
            "before": before, "query": query, "category": category}


def step(request, reply="확인해 볼게요.", intent="train_lookup"):
    return {"intent": intent, "reply": reply, "chips": [], "proposals": [], "sources": [],
            "tool_request": request, "plan": None}


def answer(reply, intent="train_lookup", proposals=(), chips=("동해역 열차 목록",)):
    return {"intent": intent, "reply": reply, "chips": list(chips),
            "proposals": list(proposals), "sources": [], "tool_request": None, "plan": None}


class _Case(unittest.TestCase):
    agent = True

    def setUp(self):
        self.settings = Settings(chat_mock=False, openai_api_key="test",
                                 llm_usage_log=os.devnull, chat_agent=self.agent)
        self.client = TestClient(create_app(self.settings))

    def ask(self, outputs, message, **context):
        with patch.object(llm, "chat_json", side_effect=list(outputs)) as chat:
            response = self.client.post(URL, json={"message": message, "context": context})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json(), chat


class LoopTests(_Case):
    def test_a_simple_question_still_ends_in_one_call(self):
        body, chat = self.ask([answer("안녕하세요.", "smalltalk")], "안녕")
        self.assertEqual(chat.call_count, 1)
        self.assertEqual(body["steps"], [])
        self.assertEqual(body["mode"], "llm")

    def test_one_tool_is_two_calls_as_before(self):
        body, chat = self.ask(
            [step(tool("station_rhythm", DONGHAE)), answer("동해역은 열차가 자주 다녀요.")],
            "동해역 열차 언제 몰려?")
        self.assertEqual(chat.call_count, 2)
        self.assertEqual([s["tool"] for s in body["steps"]], ["station_rhythm"])
        self.assertEqual(body["tool_used"], "station_rhythm")

    def test_several_tools_run_in_order_and_each_step_is_described(self):
        body, chat = self.ask(
            [step(tool("station_rhythm", DONGHAE)), step(tool("station_rhythm", MUKHO)),
             step(tool("search_places", place_id=MUKHO, category="restaurant")),
             answer("두 역 모두 열차가 있어요.")],
            "동해역이랑 묵호역 보고 묵호역 근처에서 밥 먹을 수 있어?")
        self.assertEqual(chat.call_count, 4)
        self.assertEqual([s["tool"] for s in body["steps"]],
                         ["station_rhythm", "station_rhythm", "search_places"])
        self.assertRegex(body["steps"][0]["summary"], r"^동해역 열차 리듬\(하루 \d+편, 공백 \d+번\)을 확인했어요\.$")
        self.assertIn("묵호역", body["steps"][1]["summary"])
        for item in body["steps"]:
            self.assertTrue(item["summary"].endswith("요."))  # a sentence for the user
        self.assertEqual(body["tool_used"], "station_rhythm")  # the train lookup leads

    def test_each_call_sees_every_result_so_far_and_how_many_steps_are_left(self):
        _, chat = self.ask(
            [step(tool("station_rhythm", DONGHAE)), step(tool("station_rhythm", MUKHO)),
             answer("확인했어요.")],
            "동해역이랑 묵호역 열차 비교해줘")
        second, third = (call.args[1][-1]["content"] for call in chat.call_args_list[1:])
        self.assertIn("남은 횟수 3번", second)
        self.assertEqual(second.count('"tool": "station_rhythm"'), 1)
        self.assertIn("남은 횟수 2번", third)
        self.assertEqual(third.count('"tool": "station_rhythm"'), 2)

    def test_the_step_limit_ends_with_a_call_that_must_answer(self):
        self.settings = self.settings.model_copy(update={"chat_agent_max_steps": 2})
        self.client = TestClient(create_app(self.settings))
        body, chat = self.ask(
            [step(tool("station_rhythm", DONGHAE)), step(tool("station_rhythm", MUKHO)),
             # Told to answer, the model still asks for more: the request is ignored.
             {**step(tool("station_rhythm", CHUAM)), "reply": "두 역을 확인했어요."}],
            "동해역, 묵호역, 추암역 열차 비교해줘")
        self.assertEqual(chat.call_count, 3)
        self.assertEqual(len(body["steps"]), 2)
        self.assertIn("더 확인할 수 없다", chat.call_args_list[-1].args[1][-1]["content"])
        self.assertEqual(body["reply"].split(" 최근")[0], "두 역을 확인했어요.")

    def test_the_same_lookup_twice_is_run_once(self):
        body, chat = self.ask(
            [step(tool("station_rhythm", DONGHAE)), step(tool("station_rhythm", DONGHAE)),
             answer("확인했어요.")],
            "동해역 열차 언제 몰려?")
        self.assertEqual(len(body["steps"]), 1)
        self.assertIn("더 확인할 수 없다", chat.call_args_list[-1].args[1][-1]["content"])

    def test_a_plan_request_runs_no_tools(self):
        body, chat = self.ask([step(tool("station_rhythm", DONGHAE), intent="plan_request")],
                              "일정 짜줘", pins=[DONGHAE], date="2026-11-21")
        self.assertEqual(chat.call_count, 1)
        self.assertEqual(body["intent"], "plan_request")
        self.assertEqual(body["steps"], [])


class TimeBudgetTests(_Case):
    def test_with_little_time_left_the_next_call_must_answer(self):
        with patch.object(llm.Deadline, "remaining", return_value=2.0):
            body, chat = self.ask(
                [step(tool("station_rhythm", DONGHAE)), answer("동해역을 확인했어요.")],
                "동해역이랑 묵호역 열차 비교해줘")
        self.assertEqual(chat.call_count, 2)
        self.assertIn("더 확인할 수 없다", chat.call_args_list[-1].args[1][-1]["content"])
        self.assertEqual(len(body["steps"]), 1)

    def test_out_of_time_answers_with_what_was_checked(self):
        outputs = [step(tool("station_rhythm", DONGHAE)), step(tool("station_rhythm", MUKHO)),
                   llm.LLMError("request deadline reached before step_2")]
        body, _ = self.ask(outputs, "동해역이랑 묵호역 열차 비교해줘")
        self.assertEqual(body["mode"], "llm")  # not the built-in responder: work was done
        self.assertIn("동해역 열차 리듬(", body["reply"])
        self.assertIn("묵호역 열차 리듬(", body["reply"])
        self.assertIn(service.AGENT_OUT_OF_TIME, body["reply"])
        self.assertNotIn("확인해 볼게요", body["reply"])  # the model's interim line is dropped
        self.assertEqual(len(body["steps"]), 2)
        self.assertEqual(body["proposals"], [])

    def test_failing_before_anything_was_learned_falls_back(self):
        body, _ = self.ask([llm.LLMError("boom")], "동해역 열차 언제 몰려?", focused=DONGHAE)
        self.assertEqual(body["mode"], "fallback")
        self.assertEqual(body["steps"], [])


class ToolTests(_Case):
    def test_tools_are_read_only(self):
        names = prompts.AGENT_SCHEMA["properties"]["tool_request"]["anyOf"][1]["properties"]["name"]["enum"]
        self.assertEqual(sorted(names), ["nearby_spots", "saved_items", "search_places",
                                         "search_trains", "station_rhythm", "walk_time"])
        # Nothing that saves, removes or confirms: those stay with the user's own taps.
        self.assertNotIn("도구는 읽기만 한다", prompts.build_static())
        self.assertIn("도구는 읽기만 한다", prompts.build_agent_static())

    def test_a_station_the_user_did_not_bring_up_is_not_looked_at(self):
        body, _ = self.ask(
            [step(tool("station_rhythm", MUKHO)), answer("확인하지 못했어요.")], "열차 언제 몰려?")
        self.assertEqual(body["tool_used"], None)
        self.assertIn("확인하지 못했어요", body["steps"][0]["summary"])

    def test_every_place_named_in_the_message_may_be_looked_at(self):
        body, _ = self.ask(
            [step(tool("station_rhythm", DONGHAE)), step(tool("station_rhythm", CHUAM)),
             answer("확인했어요.")],
            "동해역이랑 추암역 열차 비교해줘")
        self.assertEqual(len(body["steps"]), 2)
        self.assertNotIn("못했어요", body["steps"][1]["summary"])

    def test_trains_between_two_stations(self):
        body, chat = self.ask(
            [step(tool("search_trains", DONGHAE, MUKHO, after="17:00")), answer("열차가 있어요.")],
            "동해역에서 묵호역 가는 17시 이후 열차 있어?")
        result = chat.call_args_list[-1].args[1][-1]["content"]
        self.assertIn("동해역 출발 →", result)
        self.assertIn("묵호역 도착", result)
        self.assertRegex(body["steps"][0]["summary"], r"동해역에서 묵호역까지 가는 17:00 이후 열차 \d+편을 확인했어요")

    def test_trains_that_arrive_by_a_time_latest_first(self):
        body, chat = self.ask(
            [step(tool("search_trains", MUKHO, DONGHAE, before="19:00")), answer("열차가 있어요.")],
            "묵호역에서 동해역으로 7시 전에 돌아올 수 있어?")
        result = chat.call_args_list[-1].args[1][-1]["content"]
        listed = json.loads(result.split("\n")[1][3:])
        arrivals = [line.split("→ ")[1][:5] for line in listed["trains"]]
        self.assertTrue(arrivals and all(a <= "19:00" for a in arrivals))
        self.assertEqual(arrivals, sorted(arrivals, reverse=True))
        self.assertIn("19:00까지 도착하는", body["steps"][0]["summary"])
        self.assertIn("last_train", listed)

    def test_a_passing_station_has_no_trains_to_ride(self):
        body, chat = self.ask(
            [step(tool("search_trains", DONGHAE, MANGSANG)), answer("탈 수 있는 열차가 없어요.")],
            "동해역에서 망상역까지 열차로 갈 수 있어?")
        self.assertIn("찾지 못했어요", body["steps"][0]["summary"])
        self.assertIn("관찰", body["reply"])  # the passing-station rule still applies

    def test_saved_items_are_read_from_the_account(self):
        saved = service.SavedSelection(placeIds=("poi_nongoldam",), mustPlaceIds=("poi_nongoldam",),
                                       trains=(("1641", "ride", DONGHAE),))
        with patch.object(llm, "chat_json", side_effect=[
                step(tool("saved_items"), intent="knowledge"),
                answer("논골담길과 열차 한 편을 담아 두셨어요.", "knowledge")]) as chat:
            request = service.ChatRequest(message="내가 담아 둔 거 뭐 있어?")
            body = service.answer(request, self.settings, saved)
        result = chat.call_args_list[-1].args[1][-1]["content"]
        self.assertIn("논골담길", result)
        self.assertIn('"must": true', result)
        self.assertIn("1641", result)
        self.assertEqual(body.steps[0].summary, "담아 둔 장소 1곳과 열차 1편을 확인했어요.")
        self.assertEqual(body.reply, "논골담길과 열차 한 편을 담아 두셨어요.")  # grounded: kept

    def test_place_search_by_name(self):
        body, chat = self.ask(
            [step(tool("search_places", query="논골"), intent="knowledge"),
             answer("논골담길이 있어요.", "proposal",
                    proposals=[{"type": "add", "target": "poi_nongoldam", "reason": "찾으신 곳이에요",
                                "time_bound": False}])],
            "논골 뭐시기 있었는데 찾아줘")
        self.assertIn("poi_nongoldam", chat.call_args_list[-1].args[1][-1]["content"])
        self.assertEqual(body["steps"][0]["summary"], "장소 1곳을 찾았어요.")
        # A place a lookup returned may be proposed; it still needs the user's tap.
        self.assertEqual([(p["target"], p["needs_confirm"]) for p in body["proposals"]],
                         [("poi_nongoldam", True)])

    def test_place_search_needs_something_to_look_for(self):
        body, _ = self.ask([step(tool("search_places")), answer("찾지 못했어요.", "knowledge")],
                           "뭐 좀 찾아줘")
        self.assertIn("확인하지 못했어요", body["steps"][0]["summary"])


class ServerRulesStillApplyTests(_Case):
    def test_a_time_no_step_returned_is_not_served(self):
        body, _ = self.ask(
            [step(tool("station_rhythm", DONGHAE)), step(tool("station_rhythm", MUKHO)),
             answer("묵호역에는 03:33에 특별열차가 와요.")],
            "동해역이랑 묵호역 열차 비교해줘")
        self.assertEqual(body["reply"].split(" 최근")[0], service.NO_TIME_WITHOUT_TOOL)

    def test_a_train_number_followed_by_pyeon_is_not_a_count(self):
        # Seen on the real model: "ITX-마음 1831 편이에요" was read as "1831 trains".
        evidence = "18:34 추암역 출발 → 18:40 동해역 도착, ITX-마음 1831, 6분"
        reply = "마지막 열차는 18:34에 출발하는 ITX-마음 1831 편이에요."
        self.assertEqual(service.unverified_time_claims(reply, evidence, 3), [])
        self.assertEqual(service.unverified_time_claims("하루 1831편이 다녀요.", "하루 63편", 63),
                         ["count 1831"])  # a number the result does not have is still caught
        self.assertEqual(service.unverified_time_claims("하루 99편이 다녀요.", "99번 버스", 63),
                         ["count 99"])

    def test_a_proposal_no_step_returned_is_dropped(self):
        made_up = {"type": "add", "target": "poi_chotdaebawi", "reason": "좋아요", "time_bound": False}
        body, _ = self.ask(
            [step(tool("station_rhythm", DONGHAE)), answer("확인했어요.", proposals=[made_up])],
            "동해역 열차 언제 몰려?")
        self.assertEqual(body["proposals"], [])

    def test_the_basis_of_snapshot_times_is_always_shown(self):
        body, _ = self.ask(
            [step(tool("search_trains", DONGHAE, MUKHO)),
             step(tool("search_places", place_id=MUKHO, category="restaurant")),
             answer("열차가 있어요.")],
            "동해역에서 묵호역 가서 밥 먹을 수 있어?")
        self.assertIn("토요일 3주", body["reply"])

    def test_safety_wording_holds_inside_the_agent(self):
        body, _ = self.ask(
            [step(tool("station_rhythm", DONGHAE), intent="safety"),
             answer("차단기 안쪽으로 들어가면 잘 보여요.", "safety")],
            "동해역 건널목 안에서 열차 찍고 싶어")
        self.assertIn("권하지 않아요", body["reply"])
        self.assertNotIn("들어가면 잘 보여요", body["reply"])


class SwitchTests(_Case):
    agent = False

    def test_off_means_the_earlier_behaviour_one_tool_at_most(self):
        body, chat = self.ask(
            [step(tool("station_rhythm", DONGHAE)),
             {**step(tool("station_rhythm", MUKHO)), "reply": "동해역을 확인했어요."}],
            "동해역이랑 묵호역 열차 비교해줘")
        self.assertEqual(chat.call_count, 2)
        self.assertEqual(len(body["steps"]), 1)

    def test_off_sends_the_prompt_and_schema_unchanged(self):
        _, chat = self.ask([answer("안녕하세요.", "smalltalk")], "안녕")
        _, messages, schema, *_ = chat.call_args.args
        self.assertEqual(messages[0]["content"], prompts.build_static())
        self.assertNotIn("search_places", messages[0]["content"])
        self.assertIs(schema, prompts.DRAFT_SCHEMA)

    def test_on_adds_the_agent_rules_after_the_same_prefix(self):
        on = prompts.build_agent_static()
        self.assertTrue(on.startswith(prompts.build_static()))  # the cached prefix is shared
        self.assertIn("search_places", on)

    def test_the_setting_is_off_by_default(self):
        self.assertFalse(Settings().chat_agent)
        self.assertEqual(Settings().chat_agent_max_steps, 4)


if __name__ == "__main__":
    unittest.main()
