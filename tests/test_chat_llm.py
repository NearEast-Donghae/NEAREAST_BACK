"""LLM responder with a fake client: no key, no network, no tokens spent."""

import json
import os
import re
import time
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
        self.settings = Settings(chat_mock=False, openai_api_key="test", llm_usage_log=os.devnull)
        self.client = TestClient(create_app(self.settings))

    def ask(self, replies, message="질문", history=(), **context):
        with patch.object(llm, "chat_json", side_effect=list(replies)) as chat:
            response = self.client.post(
                URL, json={"message": message, "context": context, "history": list(history)}
            )
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

    def test_named_document_survives_the_document_limit(self):
        # Three pins already fill the six-document budget with related documents.
        _, chat = self.ask([draft(intent="smalltalk")], message="평능역 알려줘",
                           focused="station_donghae", pins=["station_mukho", "station_chuam"])
        given = sent(chat)[-2]["content"]
        self.assertIn("<heritage_pyeongneung>", given)
        self.assertIn("<station_donghae>", given)  # the open marker stays first
        self.assertLess(given.index("<station_donghae>"), given.index("<heritage_pyeongneung>"))
        self.assertEqual(len(re.findall(r"^<\w+>", given, re.MULTILINE)), 6)  # budget kept

    def test_without_a_named_document_pin_documents_are_kept(self):
        _, chat = self.ask([draft(intent="smalltalk")], message="안녕",
                           focused="station_donghae", pins=["station_mukho", "station_chuam"])
        given = sent(chat)[-2]["content"]
        for doc in ("station_donghae", "station_mukho", "station_chuam"):
            self.assertIn(f"<{doc}>", given)
        self.assertNotIn("<heritage_pyeongneung>", given)

    def trains_after(self, after):
        tool = {"name": "search_trains", "station_id": "station_donghae", "place_id": None,
                "after": after}
        response, chat = self.ask(
            [draft(intent="train_lookup", tool=tool), draft("(예시 데이터) 확인했어요.",
                                                             intent="train_lookup")],
            focused="station_donghae",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["mode"], "llm")  # answered, not a crash + fallback
        result = json.loads(sent(chat, 1)[-1]["content"].split("\n")[1])
        return [t["time"] for t in result["trains"]]

    def test_after_filter_understands_clock_formats(self):
        for after in ("16:00", "오후 4시", "16시"):
            with self.subTest(after):
                self.assertEqual(self.trains_after(after), ["16:00", "18:10"])
        self.assertEqual(self.trains_after("11시 10분"), ["11:15", "16:00", "18:10"])

    def test_unusable_after_is_ignored_instead_of_failing(self):
        for after in ("오후쯤", "soon", "25:99", "", None):
            with self.subTest(after):
                self.assertEqual(len(self.trains_after(after)), 8)  # the full list

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

    def test_proposal_answers_that_are_not_ungrounded_are_kept(self):
        nongoldam = {"type": "add", "target": "poi_nongoldam", "reason": "가까워요",
                     "time_bound": False}
        cases = (
            ("빈 시간은 그대로 둘래요", "좋아요, 빈 시간은 그대로 둘게요.", [], {}),
            ("근처 추천", "묵호역 근처에는 논골담길이 있어요.", [nongoldam],
             {"focused": "station_mukho", "depth": "reference"}),  # server drops the proposal
            ("근처 추천", "논골담길은 이미 핀에 있어요.", [nongoldam],
             {"focused": "station_mukho", "pins": ["poi_nongoldam"]}),
        )
        for message, reply, proposals, context in cases:
            with self.subTest(reply):
                response, _ = self.ask([draft(reply, intent="proposal", proposals=proposals)],
                                       message=message, **context)
                self.assertEqual(response.json()["reply"], reply)
                self.assertEqual(response.json()["proposals"], [])

    def test_ungrounded_recommendation_is_still_replaced(self):
        response, _ = self.ask([draft("해운대 근처 카페 거리가 좋아요.", intent="proposal")],
                               message="부산 카페 추천해줘")
        self.assertTrue(response.json()["reply"].startswith("아직 확인된 정보가 없어요."))

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

    def test_open_passing_station_does_not_tag_unrelated_answers(self):
        cases = (
            ("안녕", draft("안녕하세요, 동해 철도 여행을 함께 짜는 니어리예요.", intent="smalltalk")),
            ("2031년에 뭐가 바뀌어?", draft("2031년 고속화·지하화로 해안 선로 풍경이 사라질 예정이에요.",
                                       sources=["rail_2031_changes"])),
        )
        for message, reply in cases:
            with self.subTest(message):
                response, _ = self.ask([reply], message=message, focused="station_mangsang")
                body = response.json()
                self.assertEqual(body["reply"], reply["reply"])
                self.assertNotIn("safety_passing_station", body["sources"])

    def test_open_passing_station_is_explained_when_the_answer_is_about_it(self):
        cases = (
            ("여기서 탈 수 있어?", draft("망상역은 평소에 여객열차가 서지 않아요.", intent="smalltalk")),
            ("여기 뭐야?", draft("망상역은 2011년부터 여객열차가 서지 않아요.",
                              sources=["station_mangsang"])),
            ("삼화역 알려줘", draft("삼화역은 화물역이에요.", sources=["station_samhwa"])),
        )
        for message, reply in cases:
            with self.subTest(message):
                response, _ = self.ask([reply], message=message, focused="station_mangsang")
                body = response.json()
                self.assertIn("관찰만", body["reply"])
                self.assertIn("safety_passing_station", body["sources"])

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

    def test_short_station_name_puts_the_station_in_play(self):
        tool = {"name": "station_rhythm", "station_id": "station_mukho", "place_id": None,
                "after": None}
        response, _ = self.ask(
            [draft(intent="train_lookup", tool=tool),
             draft("(예시 데이터) 묵호역은 10:30에 열차가 와요.", intent="train_lookup")],
            message="묵호 열차 언제 와?",
        )
        self.assertEqual(response.json()["tool_used"], "station_rhythm")

    FOLLOW_UP = [draft(intent="train_lookup", tool={
        "name": "search_trains", "station_id": None, "place_id": None, "after": "16:00"}),
        draft("(예시 데이터) 16:00에 KTX-이음이 있어요.", intent="train_lookup")]

    def test_follow_up_uses_the_station_from_earlier_turns(self):
        history = [{"role": "user", "content": "동해역 열차 리듬"},
                   {"role": "assistant", "content": "(예시 데이터) 묵호역 쪽도 볼 수 있어요."}]
        response, _ = self.ask(self.FOLLOW_UP, message="그럼 오후 열차는?", history=history)
        body = response.json()
        self.assertEqual(body["tool_used"], "search_trains")  # 동해역, the user's own mention
        self.assertIn("16:00", body["reply"])

    def test_screen_beats_history_and_no_history_means_no_station(self):
        history = [{"role": "user", "content": "동해역 열차 리듬"}]
        response, chat = self.ask(self.FOLLOW_UP, message="그럼 오후 열차는?", history=history,
                                  focused="station_chuam")
        self.assertIn('"station_id": "station_chuam"', sent(chat, 1)[-1]["content"])
        response, _ = self.ask(self.FOLLOW_UP, message="그럼 오후 열차는?")
        self.assertIsNone(response.json()["tool_used"])  # nothing to recall: ask for a station

    def test_replaced_replies_are_logged_with_the_original_text(self):
        cases = (
            ("부산역 맛집 알려줘", draft("부산역 주변이라면 북평민속시장이 좋아요."), "no documents"),
            ("열차 언제 와?", draft("동해역은 09:13에 열차가 와요.", intent="train_lookup"),
             "unverified time"),
            ("건널목 안에서 찍어도 돼?", draft("가능할 수도 있어요.", intent="safety"), "safety"),
        )
        for message, reply, rule in cases:
            with self.subTest(rule), self.assertLogs(
                "neareast_api.modules.chat.service", "WARNING"
            ) as logs:
                self.ask([reply], message=message)
            self.assertIn(rule, logs.output[0])
            self.assertIn(reply["reply"], logs.output[0])

    def test_untouched_replies_are_not_logged(self):
        with self.assertNoLogs("neareast_api.modules.chat.service", "WARNING"):
            self.ask([draft("동해역은 분기역이에요.", sources=["station_donghae"])],
                     focused="station_donghae")
            self.ask([draft("건널목 안 촬영은 권하지 않아요. 승강장에서 보세요.", intent="safety")],
                     message="건널목 안에서 찍어도 돼?")

    def test_long_reply_is_cut_at_a_sentence_boundary(self):
        sentence = "논골담길은 묵호역 근처에 있어서 걸어서 다녀오기 좋아요."
        response, _ = self.ask([draft(" ".join([sentence] * 40), intent="smalltalk")])
        reply = response.json()["reply"]
        self.assertLessEqual(len(reply), service.MAX_REPLY_CHARS)
        self.assertTrue(reply.endswith(sentence))  # no half sentence
        self.assertGreater(reply.count(sentence), 5)

        endless = "가" * 1000  # no sentence break at all
        response, _ = self.ask([draft(endless, intent="smalltalk")])
        self.assertLessEqual(len(response.json()["reply"]), service.MAX_REPLY_CHARS)
        self.assertGreater(len(response.json()["reply"]), service.MAX_REPLY_CHARS - 10)
        self.assertTrue(response.json()["reply"].endswith("…"))

    def test_long_list_after_a_short_intro_is_shortened_not_dropped(self):
        # Seen in evaluation: "목록이에요." survived and the whole train list was cut.
        items = ", ".join(f"{h:02d}:10 무궁화 M-16{h:02d}(강릉 방면, 정차)" for h in range(5, 24))
        reply = service._clip(f"(예시 데이터) 동해역 열차 목록이에요. {items}")
        self.assertLessEqual(len(reply), service.MAX_REPLY_CHARS)
        self.assertIn("05:10 무궁화", reply)
        self.assertGreater(reply.count("무궁화"), 5)
        self.assertTrue(reply.endswith("…"))
        self.assertTrue(reply.endswith("정차)…"))  # ends between items, not inside one

    def test_normal_reply_is_not_cut(self):
        reply = "동해역은 분기역이에요. 1940년 북평역으로 문을 열었어요. 철도기념비도 있어요."
        response, _ = self.ask([draft(reply, intent="smalltalk")])
        self.assertEqual(response.json()["reply"], reply)
        self.assertEqual(service._clip("가" * service.MAX_REPLY_CHARS), "가" * service.MAX_REPLY_CHARS)

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

    def test_llm_failure_falls_back_to_builtin_responder(self):
        for error in (llm.LLMError("timeout"), RuntimeError("unexpected")):
            with self.subTest(type(error).__name__), self.assertLogs(
                "neareast_api.modules.chat.service", "WARNING"
            ):
                response, _ = self.ask([error], message="열차 언제 몰려?", focused="station_donghae")
            body = response.json()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(body["mode"], "fallback")
            self.assertEqual(body["tool_used"], "station_rhythm")  # a real, rule-checked answer

    def test_second_call_failure_also_falls_back(self):
        tool = {"name": "station_rhythm", "station_id": "station_donghae", "place_id": None,
                "after": None}
        with self.assertLogs("neareast_api.modules.chat.service", "WARNING"):
            response, _ = self.ask([draft(intent="train_lookup", tool=tool), llm.LLMError("429")],
                                   message="열차 언제 몰려?", focused="station_donghae")
        self.assertEqual(response.json()["mode"], "fallback")

    def test_successful_model_answer_is_marked_llm(self):
        response, chat = self.ask([draft("안녕하세요.", intent="smalltalk")])
        self.assertEqual(response.json()["mode"], "llm")
        self.assertEqual(chat.call_count, 1)

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

    def test_only_the_most_recent_history_reaches_the_model(self):
        history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"}
                   for i in range(31)]
        _, chat = self.ask([draft(intent="smalltalk")], message="지금", history=history)
        turns = [m["content"] for m in sent(chat)[1:-2]]
        self.assertEqual(turns, [f"m{i}" for i in range(21, 31)])  # last 10, order kept

    def test_short_history_is_sent_whole(self):
        history = [{"role": "user", "content": "A"}, {"role": "assistant", "content": "B"}]
        _, chat = self.ask([draft(intent="smalltalk")], message="지금", history=history)
        self.assertEqual([m["content"] for m in sent(chat)[1:-2]], ["A", "B"])

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
        client.with_options.return_value = client  # per-call timeout keeps the same client
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

    def test_failure_reason_is_diagnosable_without_provider_message(self):
        from openai import OpenAIError

        class FakeAuthError(OpenAIError):
            status_code = 401
            code = "invalid_api_key"
            request_id = "req_123"

        client = MagicMock()
        client.with_options.return_value = client  # per-call timeout keeps the same client
        client.chat.completions.create.side_effect = FakeAuthError(
            "Incorrect API key provided: sk-secret-value"
        )
        with patch.object(llm, "_client", return_value=client):
            with self.assertRaises(llm.LLMError) as raised:
                llm.chat_json(self.settings, [], {}, "first")
        message = str(raised.exception)
        for expected in ("FakeAuthError", "status_code=401", "code=invalid_api_key",
                         "request_id=req_123", f"model={self.settings.chat_model}"):
            self.assertIn(expected, message)
        self.assertNotIn("sk-secret-value", message)

    def test_failure_without_status_details_still_names_the_error(self):
        from openai import OpenAIError

        client = MagicMock()
        client.with_options.return_value = client  # per-call timeout keeps the same client
        client.chat.completions.create.side_effect = OpenAIError("connection reset")
        with patch.object(llm, "_client", return_value=client):
            with self.assertRaises(llm.LLMError) as raised:
                llm.chat_json(self.settings, [], {}, "first")
        self.assertEqual(str(raised.exception),
                         f"chat call failed: OpenAIError model={self.settings.chat_model}")
        self.assertEqual(client.chat.completions.create.call_count, 2)  # transient: one retry

    def test_transient_failure_is_retried_once_and_client_errors_are_not(self):
        from openai import OpenAIError

        class Unauthorized(OpenAIError):
            status_code = 401

        good = self.fake_client(json.dumps({"ok": True}))
        ok_response = good.chat.completions.create.return_value
        good.chat.completions.create.side_effect = [OpenAIError("timeout"), ok_response]
        with patch.object(llm, "_client", return_value=good), self.assertLogs(
            "neareast_api.modules.chat.llm", "WARNING"
        ):
            self.assertEqual(llm.chat_json(self.settings, [], {}, "first"), {"ok": True})
        self.assertEqual(good.chat.completions.create.call_count, 2)
        self.assertEqual(good.with_options.call_args.kwargs["max_retries"], 0)

        denied = MagicMock()
        denied.with_options.return_value = denied
        denied.chat.completions.create.side_effect = Unauthorized("no")
        with patch.object(llm, "_client", return_value=denied):
            with self.assertRaises(llm.LLMError):
                llm.chat_json(self.settings, [], {}, "first")
        self.assertEqual(denied.chat.completions.create.call_count, 1)

    def test_call_timeout_fits_inside_the_request_deadline(self):
        client = self.fake_client(json.dumps({"ok": True}))
        with patch.object(llm, "_client", return_value=client):
            llm.chat_json(self.settings, [], {}, "first", llm.Deadline(3))
            self.assertLessEqual(client.with_options.call_args.kwargs["timeout"], 3)
            llm.chat_json(self.settings, [], {}, "first", llm.Deadline(60))
            self.assertEqual(client.with_options.call_args.kwargs["timeout"],
                             self.settings.llm_timeout_seconds)  # never above the per-call cap
            calls = client.chat.completions.create.call_count
            with self.assertRaises(llm.LLMError) as raised:
                llm.chat_json(self.settings, [], {}, "after_tool", llm.Deadline(0.1))
            self.assertIn("deadline", str(raised.exception))
            self.assertEqual(client.chat.completions.create.call_count, calls)  # not even started

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



class DeadlineAndTimingTests(unittest.TestCase):
    """Whole-request budget and the per-stage record, through the real service path."""

    TOOL = {"name": "station_rhythm", "station_id": "station_donghae", "place_id": None,
            "after": None}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "usage.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def client_with(self, delay, **settings):
        """A fake OpenAI client: first answer requests a tool, second is final."""
        replies = [draft(intent="train_lookup", tool=self.TOOL),
                   draft("(예시 데이터) 10:20에 ITX-마음이 와요.", intent="train_lookup")]
        usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)

        def create(**kwargs):
            time.sleep(delay)
            content = json.dumps(replies[min(create.calls, 1)])
            create.calls += 1
            return SimpleNamespace(usage=usage, choices=[SimpleNamespace(
                finish_reason="stop", message=SimpleNamespace(content=content))])

        create.calls = 0
        fake = MagicMock()
        fake.with_options.return_value = fake
        fake.chat.completions.create.side_effect = create
        app = create_app(Settings(chat_mock=False, openai_api_key="test",
                                  llm_usage_log=str(self.log), **settings))
        return TestClient(app), fake

    def ask(self, client, fake):
        with patch.object(llm, "_client", return_value=fake):
            return client.post(URL, json={"message": "열차 언제 몰려?",
                                          "context": {"focused": "station_donghae"}})

    def requests_logged(self):
        lines = [json.loads(line) for line in self.log.read_text("utf-8").splitlines()]
        return [e for e in lines if e["kind"] == "request"]

    def test_over_budget_request_falls_back_in_time(self):
        client, fake = self.client_with(delay=0.4, chat_deadline_seconds=0.7)
        started = time.perf_counter()
        with self.assertLogs("neareast_api.modules.chat.service", "WARNING") as logs:
            body = self.ask(client, fake).json()
        self.assertLess(time.perf_counter() - started, 1.5)
        self.assertEqual(body["mode"], "fallback")
        self.assertEqual(body["tool_used"], "station_rhythm")  # still a useful answer
        self.assertIn("deadline", logs.output[0])
        self.assertEqual(fake.chat.completions.create.call_count, 1)  # second call never started
        record = self.requests_logged()[0]
        self.assertEqual(record["mode"], "fallback")
        self.assertIn("deadline", record["failure"])
        self.assertIn("fallback", record["stages"])

    def test_request_within_budget_is_answered_by_the_model_and_timed(self):
        client, fake = self.client_with(delay=0.05)
        with self.assertNoLogs("neareast_api.modules.chat.service", "WARNING"):
            body = self.ask(client, fake).json()
        self.assertEqual(body["mode"], "llm")
        record = self.requests_logged()[0]
        self.assertIsNone(record["failure"])
        self.assertEqual(set(record["stages"]),
                         {"retrieval", "llm_first", "tool", "llm_after_tool", "finalize"})
        self.assertGreaterEqual(record["stages"]["llm_first"], 50)
        self.assertGreaterEqual(record["total_ms"], 100)

    def test_slow_request_is_flagged_and_mock_mode_writes_no_record(self):
        client, fake = self.client_with(delay=0.05)
        with patch.object(service, "SLOW_REQUEST_MS", 10), self.assertLogs(
            "neareast_api.modules.chat.service", "WARNING"
        ) as logs:
            self.ask(client, fake)
        self.assertIn("slow chat request", logs.output[0])
        self.assertIn("llm_first", logs.output[0])

        quiet = Path(self.tmp.name) / "mock.jsonl"
        mock_client = TestClient(create_app(Settings(llm_usage_log=str(quiet))))
        mock_client.post(URL, json={"message": "안녕"})
        self.assertFalse(quiet.exists())


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
