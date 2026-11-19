"""LLM responder with a fake client: no key, no network, no tokens spent."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from neareast_api.core.config import Settings
from neareast_api.main import create_app
from neareast_api.modules.chat import llm, service

URL = "/api/v1/chat/messages"


def sent(chat, call=-1):
    """Messages passed to llm.chat_json in the given call."""
    return chat.call_args_list[call].args[1]


def text(messages):
    return "\n".join(m["content"] for m in messages)


def draft(reply="좋아요.", intent="knowledge", chips=("다음",), proposals=(), sources=(), tool=None):
    return {
        "intent": intent, "reply": reply, "chips": list(chips),
        "proposals": [dict(p) for p in proposals], "sources": list(sources), "tool_request": tool,
    }


class LLMResponderTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(create_app(Settings(chat_mock=False, openai_api_key="test")))

    def ask(self, replies, message="질문", **context):
        with patch.object(llm, "chat_json", side_effect=list(replies)) as chat:
            response = self.client.post(URL, json={"message": message, "context": context})
        return response, chat

    def test_single_call_without_tool_and_sources_limited_to_given_docs(self):
        response, chat = self.ask(
            [draft("동해역은 분기역이에요.", sources=["station_donghae", "invented_doc"])],
            focused="station_donghae",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(chat.call_count, 1, "one message = one LLM call")
        body = response.json()
        self.assertEqual(body["sources"], ["station_donghae"])
        self.assertIsNone(body["tool_used"])
        messages = sent(chat)
        self.assertNotIn("station_donghae (동해역)", messages[0]["content"])  # static stays static
        context = messages[-2]["content"]
        self.assertIn("<station_donghae> 동해역", context)
        self.assertIn("보고 있는 것: station_donghae (동해역)", context)
        self.assertEqual(messages[-1], {"role": "user", "content": "질문"})

    def test_tool_request_runs_tool_then_second_call_and_server_sets_certainty(self):
        tool = {"name": "station_rhythm", "station_id": "station_donghae", "place_id": None, "after": None}
        proposal = {"type": "add", "target": "poi_bukpyeong_market", "reason": "공백에 다녀와요",
                    "time_bound": True}
        response, chat = self.ask(
            [draft("확인해 볼게요.", intent="train_lookup", tool=tool),
             draft("(예시 데이터) 11:15~16:00에 열차가 없어요.", intent="train_lookup",
                   proposals=[proposal], tool=tool)],
            focused="station_donghae",
        )
        body = response.json()
        self.assertEqual(chat.call_count, 2)
        self.assertEqual(body["tool_used"], "station_rhythm")
        self.assertIn("11:15", body["reply"])
        self.assertEqual(body["proposals"][0]["certainty"], "timetable")
        first, second = sent(chat, 0), sent(chat, 1)
        self.assertEqual(second[: len(first)], first, "post-tool call must extend the cached prefix")
        self.assertEqual(second[-1]["role"], "system")
        self.assertIn('"gaps"', second[-1]["content"])

    def test_time_claim_without_tool_is_replaced(self):
        response, _ = self.ask([draft("동해역은 10:20에 열차가 와요.", intent="train_lookup")])
        body = response.json()
        self.assertNotIn("10:20", body["reply"])
        self.assertIn("확인된 시간표", body["reply"])

    def test_time_not_in_the_tool_result_is_replaced_even_after_a_tool(self):
        nearby = {"name": "nearby_spots", "station_id": None, "place_id": "station_donghae",
                  "after": None}
        rhythm = {"name": "station_rhythm", "station_id": "station_donghae", "place_id": None,
                  "after": None}
        for tool, reply in (
            (nearby, "북평민속시장이 가까워요. 참고로 KTX는 09:13에 출발해요."),  # tool has no times
            (rhythm, "(예시 데이터) 동해역은 09:13에 KTX가 와요."),  # not in the timetable
            (rhythm, "(예시 데이터) 동해역에는 하루 30편이 다녀요."),  # more than the result has
        ):
            with self.subTest(reply):
                response, _ = self.ask(
                    [draft(intent="train_lookup", tool=tool), draft(reply, intent="train_lookup")],
                    focused="station_donghae",
                )
                self.assertIn("확인된 시간표", response.json()["reply"])
                self.assertNotIn("09:13", response.json()["reply"])

    def test_durations_and_verified_times_are_kept(self):
        nearby = {"name": "nearby_spots", "station_id": None, "place_id": "station_donghae",
                  "after": None}
        rhythm = {"name": "station_rhythm", "station_id": "station_donghae", "place_id": None,
                  "after": None}
        for tool, reply in (
            (nearby, "(예시 데이터) 북평민속시장은 도보 10분 거리예요."),
            (rhythm, "(예시 데이터) 동해역은 10시대에 3편이 몰리고 오후 4시에도 열차가 있어요."),
            (rhythm, "(예시 데이터) 10시 20분에 ITX-마음이 와요. 한 바퀴 도는 데 1시간쯤 걸려요."),
        ):
            with self.subTest(reply):
                response, _ = self.ask(
                    [draft(intent="train_lookup", tool=tool), draft(reply, intent="train_lookup")],
                    focused="station_donghae",
                )
                self.assertEqual(response.json()["reply"], reply)

    def test_time_check_helper(self):
        check = service.unverified_time_claims
        doc = "입장 마감은 17:30이에요. 하루 2편의 공연이 있어요."
        self.assertEqual(check("입장 마감은 오후 5시 반이에요.", doc, None), [])  # cited document
        self.assertEqual(check("2편의 공연이 있어요.", doc, None), [])
        self.assertEqual(check("추암역에서 도보 7분, 강릉에서 1시간 거리예요. 3분의 1 정도예요.", "", None), [])
        self.assertEqual(check("1940년 2월에 문을 열었어요.", "", None), [])
        self.assertTrue(check("입장 마감은 18:00이에요.", doc, None))
        self.assertTrue(check("2편의 영화 촬영지예요.", "", None))
        self.assertTrue(check("새벽에도 5시에 와요.", "07:40 10:20", 2))

    def test_model_cannot_break_plan_rules(self):
        bad = [
            {"type": "add", "target": "poi_nongoldam", "reason": "거절했던 곳", "time_bound": False},
            {"type": "add", "target": "poi_made_up", "reason": "없는 곳", "time_bound": False},
        ]
        response, _ = self.ask(
            [draft(intent="smalltalk", proposals=bad, chips=["a", "b", "c", "d"])],
            dismissed=["poi_nongoldam"],
        )
        body = response.json()
        self.assertEqual(body["proposals"], [])
        self.assertEqual(len(body["chips"]), 3)

    def test_ungrounded_knowledge_becomes_fixed_no_info_reply(self):
        # The gpt-4.1-mini failure: "부산역 맛집" answered with a Donghae market.
        response, _ = self.ask(
            [draft("부산역 주변이라면 북평민속시장 같은 곳을 가볼 수 있어요.",
                   chips=["부산역 근처 맛집", "동해역 열차 리듬"])],
            message="부산역 맛집 알려줘",
        )
        body = response.json()
        self.assertTrue(body["reply"].startswith("아직 확인된 정보가 없어요."))
        self.assertNotIn("북평민속시장", body["reply"])
        # chips about unknown places dropped, then padded to two
        self.assertEqual(body["chips"], ["동해역 열차 리듬", "동해역 근처"])

    def test_mock_tool_answer_is_always_labelled(self):
        tool = {"name": "search_trains", "station_id": "station_chuam", "place_id": None, "after": None}
        response, _ = self.ask(
            [draft(intent="train_lookup", tool=tool),
             draft("추암역에는 08:05에 무궁화가 와요.", intent="train_lookup")],
            focused="station_chuam",
        )
        self.assertTrue(response.json()["reply"].startswith("(예시 데이터) "))

    def test_passing_station_adds_rule_source_and_observation_note(self):
        tool = {"name": "station_rhythm", "station_id": "station_mangsang", "place_id": None,
                "after": None}
        response, chat = self.ask(
            [draft(intent="train_lookup", tool=tool),
             draft("(예시 데이터) 망상역은 10:26에 열차가 지나가요.", intent="train_lookup")],
            message="망상역 열차 시간",
        )
        body = response.json()
        self.assertIn("safety_passing_station", body["sources"])
        self.assertIn("관찰만", body["reply"])
        self.assertIn("<safety_passing_station>", sent(chat, 0)[-2]["content"])  # model saw the rule

    def test_rhythm_result_offers_nearby_candidates_without_pins_or_dismissed(self):
        tool = {"name": "station_rhythm", "station_id": "station_donghae", "place_id": None,
                "after": None}
        _, chat = self.ask(
            [draft(intent="train_lookup", tool=tool), draft("(예시 데이터) ok", intent="train_lookup")],
            dismissed=["poi_cheongok_cave"], focused="station_donghae",
        )
        result = json.loads(sent(chat, 1)[-1]["content"].split("\n")[1])
        self.assertEqual([c["id"] for c in result["nearby"]], ["poi_bukpyeong_market"])

    def test_dead_end_nearby_chips_are_dropped(self):
        response, _ = self.ask(
            [draft(intent="smalltalk", chips=["삼화역 근처", "북평민속시장 근처", "묵호역 근처"])]
        )
        self.assertEqual(response.json()["chips"], ["묵호역 근처", "동해역 열차 리듬"])

    def test_hedged_safety_answer_is_replaced_and_gets_an_alternative(self):
        # gpt-5.4-nano once answered "기다리면서 찍는 건 가능할 수도 있지만…".
        response, _ = self.ask([draft("하평 건널목에서 찍는 건 가능할 수도 있지만 조심해요.",
                                      intent="safety")], message="하평 건널목에서 찍어도 돼?")
        body = response.json()
        self.assertTrue(body["reply"].startswith("선로·건널목 안이나 차단기 안쪽"))
        self.assertIn("권하지 않아요", body["reply"])
        self.assertIn("공개된 장소", body["reply"])
        self.assertNotIn("가능할 수도", body["reply"])
        self.assertIn("safety_track_crossing", body["sources"])

    def test_clear_refusal_without_alternative_gets_one_appended(self):
        response, _ = self.ask([draft("차단기 안쪽에서 기다리는 건 권하지 않아요.", intent="safety")],
                               message="차단기 안쪽에서 기다려도 돼?")
        self.assertEqual(
            response.json()["reply"],
            "차단기 안쪽에서 기다리는 건 권하지 않아요. "
            "대신 역 맞이방이나 승강장처럼 공개된 장소에서 열차를 보는 방법을 권해요.",
        )

    def test_safe_question_labelled_safety_keeps_its_answer(self):
        # No track/crossing word in the question: the rewrite must not fire.
        for message, reply in (
            ("동해역 승강장에서 사진 찍어도 돼?", "네, 승강장에서는 안전선 뒤에서 찍으셔도 괜찮아요."),
            ("안전하게 볼 수 있는 곳", "역 맞이방에서 보는 방법이 있어요."),
        ):
            with self.subTest(message):
                response, _ = self.ask([draft(reply, intent="safety")], message=message)
                body = response.json()
                self.assertEqual(body["reply"], reply)
                self.assertNotIn("safety_track_crossing", body["sources"])

    def test_tool_cannot_pick_a_station_nobody_put_in_play(self):
        # gpt-5.4-nano guessed Donghae for "여기 열차 언제 와?" with nothing open.
        tool = {"name": "station_rhythm", "station_id": "station_donghae", "place_id": None,
                "after": None}
        response, chat = self.ask(
            [draft(intent="train_lookup", tool=tool),
             draft("어느 역을 볼까요? 지도에서 역을 열어 주세요.", intent="train_lookup")],
            message="여기 열차 언제 와?",
        )
        self.assertIsNone(response.json()["tool_used"])
        self.assertIn("지도에서 역을 열어", sent(chat, 1)[-1]["content"])

    def test_list_markers_and_line_breaks_become_prose(self):
        response, _ = self.ask([draft(
            "목록이에요.\n\n- 논골담길이 있어요\n- 도째비골도 있어요\n1. 끝이에요", intent="smalltalk")])
        self.assertEqual(response.json()["reply"],
                         "목록이에요. 논골담길이 있어요 도째비골도 있어요 끝이에요")

    def test_greeting_and_markdown_are_removed_after_the_first_turn(self):
        with patch.object(llm, "chat_json", return_value=draft(
            "안녕하세요, 동해 철도 여행을 함께 짜는 니어리예요. **논골담길**이 좋아요.",
            intent="smalltalk",
        )):
            body = self.client.post(URL, json={
                "message": "고마워", "history": [{"role": "user", "content": "안녕"}],
            }).json()
        self.assertEqual(body["reply"], "논골담길이 좋아요.")

    def test_unknown_station_in_tool_request_is_not_reported_as_tool_used(self):
        tool = {"name": "station_rhythm", "station_id": "station_ghost", "place_id": None, "after": None}
        response, chat = self.ask([draft(tool=tool), draft("그 역은 아직 몰라요.")])
        self.assertIsNone(response.json()["tool_used"])
        self.assertIn("기준 역이 없어요", sent(chat, 1)[-1]["content"])

    def test_llm_failure_is_503(self):
        response, _ = self.ask([llm.LLMError("timeout")])
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"detail": "chat unavailable"})

    def test_history_is_sent_in_order(self):
        with patch.object(llm, "chat_json", return_value=draft()) as chat:
            self.client.post(URL, json={
                "message": "그럼?",
                "history": [{"role": "user", "content": "A"}, {"role": "assistant", "content": "B"}],
            })
        roles = [m["role"] for m in sent(chat)]
        self.assertEqual(roles, ["system", "user", "assistant", "system", "user"])
        self.assertEqual([m["content"] for m in sent(chat)[1:3]], ["A", "B"])
        self.assertEqual(sent(chat)[-1]["content"], "그럼?")

    def test_static_prefix_is_identical_across_requests(self):
        with patch.object(llm, "chat_json", return_value=draft()) as chat:
            self.client.post(URL, json={"message": "안녕", "context": {"focused": "station_mukho"}})
            self.client.post(URL, json={
                "message": "평능역?", "context": {"pins": ["station_donghae"], "date": "2026-11-21",
                                                  "dismissed": ["poi_nongoldam"]},
            })
        first, second = sent(chat, 0)[0], sent(chat, 1)[0]
        self.assertEqual(first, second)
        self.assertNotIn("2026-11-21", text([first]))


class UsageRecordTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "usage.jsonl"
        self.settings = Settings(chat_mock=False, openai_api_key="test", llm_usage_log=str(self.log))

    def tearDown(self):
        self.tmp.cleanup()

    def fake_client(self, content, finish="stop"):
        usage = SimpleNamespace(prompt_tokens=120, completion_tokens=30, total_tokens=150)
        response = SimpleNamespace(
            usage=usage,
            choices=[SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=content))],
        )
        client = MagicMock()
        client.chat.completions.create.return_value = response
        return client

    def test_every_call_appends_token_usage(self):
        client = self.fake_client(json.dumps({"ok": True}))
        with patch.object(llm, "_client", return_value=client):
            self.assertEqual(llm.chat_json(self.settings, [], {}, "first"), {"ok": True})
            llm.chat_json(self.settings, [], {}, "after_tool")
        entries = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual([e["kind"] for e in entries], ["chat:first", "chat:after_tool"])
        self.assertEqual(entries[0]["total_tokens"], 150)
        self.assertEqual(entries[0]["model"], self.settings.chat_model)
        kwargs = client.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["max_completion_tokens"], self.settings.chat_max_tokens)
        self.assertEqual(kwargs["response_format"]["json_schema"]["strict"], True)

    def test_reasoning_effort_is_sent_only_when_set(self):
        client = self.fake_client(json.dumps({"ok": True}))
        with patch.object(llm, "_client", return_value=client):
            llm.chat_json(self.settings, [], {}, "first")
            self.assertNotIn("reasoning_effort", client.chat.completions.create.call_args.kwargs)
            low = self.settings.model_copy(update={"chat_reasoning_effort": "low"})
            llm.chat_json(low, [], {}, "first")
            self.assertEqual(client.chat.completions.create.call_args.kwargs["reasoning_effort"], "low")
        entries = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertIsInstance(entries[0]["latency_ms"], int)

    def test_cut_off_or_invalid_answers_raise(self):
        for content, finish in (("{}", "length"), ("not json", "stop")):
            with self.subTest(finish=finish), patch.object(
                llm, "_client", return_value=self.fake_client(content, finish)
            ):
                with self.assertRaises(llm.LLMError):
                    llm.chat_json(self.settings, [], {}, "first")
        self.assertEqual(len(self.log.read_text().splitlines()), 2, "usage is recorded even then")

    def test_missing_key_never_creates_a_client(self):
        with patch.object(llm, "_client") as factory:
            with self.assertRaises(llm.LLMError):
                llm.chat_json(Settings(openai_api_key=""), [], {}, "first")
        factory.assert_not_called()

    def test_key_is_not_in_settings_repr(self):
        self.assertNotIn("sk-not-real", repr(Settings(openai_api_key="sk-not-real")))



class EvalCostTests(unittest.TestCase):
    def test_cached_tokens_are_priced_lower(self):
        import importlib.util

        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location("eval_chat", root / "scripts" / "eval_chat.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        entry = {"model": "gpt-4.1-mini", "prompt_tokens": 2_000_000,
                 "cached_tokens": 1_000_000, "completion_tokens": 1_000_000}
        # 1M uncached * 0.40 + 1M cached * 0.10 + 1M output * 1.60
        self.assertAlmostEqual(module.estimate_cost([entry]), 2.10)
        self.assertIsNone(module.estimate_cost([{**entry, "model": "unknown"}]))


if __name__ == "__main__":
    unittest.main()
