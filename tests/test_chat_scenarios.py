"""Run tests/chat_scenarios against the API; rerun after every prompt change (CLAUDE.md §7)."""

import json
import re
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from neareast_api.core.config import Settings
from neareast_api.main import create_app

URL = "/api/v1/chat/messages"
SCENARIOS = json.loads(
    (Path(__file__).parent / "chat_scenarios" / "scenarios.json").read_text("utf-8")
)["scenarios"]
TIME_CLAIM = re.compile(r"\d{1,2}:\d{2}|\d+편|\d+시대")


class ChatScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(create_app(Settings()))

    def ask(self, request):
        response = self.client.post(URL, json=request)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.check_invariants(request, body)
        return body

    def check_invariants(self, request, body):
        dismissed = set(request.get("context", {}).get("dismissed", []))
        self.assertLessEqual(len(body["chips"]), 3, "Q2: at most 3 chips")
        for proposal in body["proposals"]:
            self.assertIs(proposal["needs_confirm"], True, "Q3: proposals need confirm")
            self.assertNotIn(proposal["target"], dismissed, "Q3: dismissed re-proposed")
        if TIME_CLAIM.search(body["reply"]):
            self.assertIsNotNone(body["tool_used"], f"Q4: time claim without tool: {body['reply']}")

    def test_scenarios(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario["id"]):
                self.run_scenario(scenario)

    def run_scenario(self, scenario):
        expect = scenario["expect"]
        body = self.ask(scenario["request"])
        for index in scenario.get("chain", []):
            self.assertGreater(len(body["chips"]), index, f"no chip #{index}: {body}")
            follow = {"message": body["chips"][index], "context": scenario["request"]["context"]}
            body = self.ask(follow)
            if expect.get("each_step_has_chips"):
                self.assertTrue(body["chips"], f"chain dead-ends: {body}")
                self.assertNotIn("확인된 정보가 없어요", body["reply"], "chip led nowhere")
        if "intent" in expect:
            self.assertEqual(body["intent"], expect["intent"], body["reply"])
        if "tool_used" in expect:
            self.assertEqual(body["tool_used"], expect["tool_used"])
        if "sources" in expect:
            self.assertEqual(body["sources"], expect["sources"])
        for text in expect.get("reply_contains", []):
            self.assertIn(text, body["reply"])
        targets = {p["target"] for p in body["proposals"]}
        for target in expect.get("proposal_targets_exclude", []):
            self.assertNotIn(target, targets)
        if "proposal_reason_contains" in expect:
            self.assertTrue(body["proposals"], "expected a proposal")
            for proposal in body["proposals"]:
                for text in expect["proposal_reason_contains"]:
                    self.assertIn(text, proposal["reason"])


if __name__ == "__main__":
    unittest.main()
