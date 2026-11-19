"""Chat contract, HTTP semantics and server-side plan rules, all in mock mode (no keys)."""

import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from neareast_api.core.config import Settings
from neareast_api.main import create_app
from neareast_api.modules.chat import retrieval, service
from neareast_api.modules.chat.schemas import ChatContext

URL = "/api/v1/chat/messages"


def post(client, message, **context):
    return client.post(URL, json={"message": message, "context": context})


class ChatHttpTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(create_app(Settings()))

    def test_contract_example_round_trips(self):
        response = self.client.post(
            URL,
            json={
                "message": "언제가 좋아?",
                "context": {
                    "date": "2026-11-21",
                    "pins": ["station_donghae"],
                    "focused": "station_donghae",
                    "depth": "light",
                    "dismissed": [],
                },
                "history": [{"role": "user", "content": "안녕"}],
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            set(body), {"reply", "intent", "chips", "proposals", "sources", "tool_used", "mode"}
        )
        self.assertEqual(body["mode"], "mock")
        self.assertEqual(body["intent"], "train_lookup")
        self.assertEqual(body["tool_used"], "station_rhythm")
        self.assertLessEqual(len(body["chips"]), 3)
        for proposal in body["proposals"]:
            self.assertEqual(
                set(proposal), {"type", "target", "reason", "needs_confirm", "certainty"}
            )
            self.assertIs(proposal["needs_confirm"], True)

    def test_context_and_history_are_optional(self):
        response = self.client.post(URL, json={"message": "안녕"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["intent"], "smalltalk")

    def test_invalid_input_is_422(self):
        too_long_history = [{"role": "user", "content": "x"}] * 201
        cases = {
            "empty message": {"message": ""},
            "missing message": {"context": {}},
            "bad depth": {"message": "hi", "context": {"depth": "deep"}},
            "bad date": {"message": "hi", "context": {"date": "11/21"}},
            "bad role": {"message": "hi", "history": [{"role": "system", "content": "x"}]},
            "history too long": {"message": "hi", "history": too_long_history},
            "unknown top-level field": {"message": "hi", "session": "abc"},
            "unknown context field": {"message": "hi", "context": {"focus": "station_donghae"}},
        }
        for name, payload in cases.items():
            with self.subTest(name):
                self.assertEqual(self.client.post(URL, json=payload).status_code, 422)

    def test_long_history_is_accepted(self):
        history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"}
                   for i in range(31)]
        response = self.client.post(URL, json={"message": "안녕", "history": history})
        self.assertEqual(response.status_code, 200)

    def test_unknown_ids_are_ignored_and_disclosed_not_404(self):
        response = post(
            self.client, "여기 열차 언제 와?",
            focused="station_nowhere", pins=["poi_ghost", "station_mukho"],
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["reply"].startswith("지금 열어 둔 장소는 아직 모르는 곳이라"))
        self.assertNotIn("station_nowhere", body["reply"])  # internal ids stay out of the chat
        self.assertNotIn("poi_ghost", body["reply"])
        self.assertIn("묵호역", body["reply"])  # falls back to the known pin
        self.assertNotIn("station_nowhere", body["sources"])
        self.assertNotIn("poi_ghost", body["sources"])

    def test_unknown_place_notice_wording_and_repetition(self):
        with self.assertLogs("neareast_api.modules.chat.service", "WARNING") as logs:
            pins_only = post(self.client, "안녕", pins=["poi_ghost"]).json()
        self.assertTrue(pins_only["reply"].startswith("핀 가운데 아직 모르는 곳이 있어"))
        self.assertIn("poi_ghost", logs.output[0])  # developers still see which id
        told = self.client.post(URL, json={
            "message": "안녕", "context": {"focused": "station_nowhere"},
            "history": [{"role": "user", "content": "여기 뭐야?"},
                        {"role": "assistant", "content": pins_only["reply"]}],
        }).json()
        self.assertNotIn("모르는 곳", told["reply"])  # said once per conversation
        known = post(self.client, "안녕", focused="station_donghae").json()
        self.assertNotIn("모르는 곳", known["reply"])

    def test_dismissed_target_is_never_proposed_again(self):
        first = post(self.client, "근처 추천해줘", focused="station_mukho").json()
        targets = [p["target"] for p in first["proposals"]]
        self.assertIn("poi_nongoldam", targets)
        again = post(
            self.client, "근처 추천해줘", focused="station_mukho", dismissed=["poi_nongoldam"]
        ).json()
        self.assertNotIn("poi_nongoldam", [p["target"] for p in again["proposals"]])

    def test_certainty_comes_from_tool_and_is_null_when_time_free(self):
        timed = post(self.client, "열차 언제 몰려?", focused="station_donghae").json()
        self.assertEqual([p["certainty"] for p in timed["proposals"]], ["timetable"])
        nearby = post(self.client, "근처 추천", focused="station_mukho").json()
        self.assertTrue(nearby["proposals"])
        self.assertTrue(all(p["certainty"] is None for p in nearby["proposals"]))

    def test_train_list_chip_uses_search_tool(self):
        body = post(self.client, "그 시간 열차 목록", focused="station_donghae").json()
        self.assertEqual(body["tool_used"], "search_trains")
        self.assertEqual(body["proposals"], [])

    def test_passing_station_is_observation_only(self):
        body = post(self.client, "열차 시간 알려줘", focused="station_mangsang").json()
        self.assertIn("관찰만", body["reply"])

    def test_safety_request_redirects_to_public_alternative(self):
        body = post(self.client, "건널목 안에서 찍어도 돼?", focused="station_donghae").json()
        self.assertEqual(body["intent"], "safety")
        self.assertIn("권하지 않아요", body["reply"])
        self.assertIn("공개된 장소", body["reply"])

    def test_llm_mode_without_key_falls_back_to_builtin_answers(self):
        # No key: must fail fast with 503 and never reach the network.
        with patch.dict(os.environ, {"NEAREAST_CHAT_MOCK": "false", "NEAREAST_OPENAI_API_KEY": ""}):
            client = TestClient(create_app())
        with self.assertLogs("neareast_api.modules.chat.service", "WARNING"):
            response = client.post(URL, json={"message": "안녕"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["mode"], "fallback")

    def test_persona_gives_name_greeting_and_loading_texts(self):
        response = self.client.get("/api/v1/chat/persona")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["name"], "니어리")
        self.assertEqual(body["greeting"], "안녕하세요, 동해 철도 여행을 함께 짜는 니어리예요.")
        self.assertEqual(
            set(body["loading"]), {"default", "train", "place", "knowledge", "safety", "slow"}
        )
        self.assertIn("{place}", body["loading"]["place"])
        safety = body["loading"]["safety"]
        for playful in ("~", "!", "니어리", "ㅎ", "♪"):
            self.assertNotIn(playful, safety)

    def test_mock_greeting_matches_persona(self):
        greeting = self.client.get("/api/v1/chat/persona").json()["greeting"]
        self.assertTrue(post(self.client, "안녕").json()["reply"].startswith(greeting))

    def test_openapi_documents_contract_and_503(self):
        schema = self.client.get("/openapi.json").json()
        operation = schema["paths"][URL]["post"]
        self.assertIn("503", operation["responses"])
        self.assertIn("422", operation["responses"])
        context = schema["components"]["schemas"]["ChatContext"]["properties"]
        self.assertIn("dismissed", context)


class FinalizeRuleTests(unittest.TestCase):
    """A responder (later: the LLM) may break rules; _finalize must still hold them."""

    def finalize(self, draft, **context):
        resolved = service._resolve(ChatContext(**context), retrieval.load_content("."))
        return service._finalize(draft, resolved)

    def test_rule_breaking_draft_is_corrected(self):
        draft = service.Draft(
            reply="ok",
            intent="proposal",
            chips=["a", "b", "c", "d"],
            proposals=[
                service.DraftProposal("add", "poi_nongoldam", "dismissed", time_bound=True),
                service.DraftProposal("add", "poi_unknown", "unknown target"),
                service.DraftProposal("add", "station_mukho", "already pinned"),
                service.DraftProposal("remove", "poi_dojjebigol", "not pinned"),
                service.DraftProposal("add", "poi_chotdaebawi", "kept", time_bound=True),
                service.DraftProposal("remove", "station_mukho", "kept, time-free"),
            ],
            sources=["station_mukho", "made_up_doc", "station_mukho"],
            tool_used="station_rhythm",
            tool_certainty="planned",
        )
        response = self.finalize(
            draft, pins=["station_mukho"], dismissed=["poi_nongoldam"]
        )
        self.assertEqual(response.chips, ["a", "b", "c"])
        self.assertEqual(
            [(p.type, p.target, p.certainty, p.needs_confirm) for p in response.proposals],
            [
                ("add", "poi_chotdaebawi", "planned", True),
                ("remove", "station_mukho", None, True),
            ],
        )
        self.assertEqual(response.sources, ["station_mukho"])

    def test_reference_depth_never_proposes(self):
        draft = service.Draft(
            reply="ok",
            intent="proposal",
            proposals=[service.DraftProposal("add", "poi_nongoldam", "x")],
        )
        self.assertEqual(self.finalize(draft, depth="reference").proposals, [])


if __name__ == "__main__":
    unittest.main()
