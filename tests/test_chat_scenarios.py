"""Quality scenarios (tests/chat_scenarios). Unit tests run them in mock mode;
scripts/eval_chat.py runs the same checks against the real LLM.

Expectation keys (all optional):
  intent, tool_used, tool_used_in, sources, sources_include, sources_include_any,
  reply_contains, reply_contains_any, reply_not_contains, no_proposals,
  proposal_targets_exclude, proposal_reason_contains, proposal_certainty_not_null,
  polite, each_step_has_chips
Scenario keys: llm_only (skipped in mock mode, reason in _note), _note (why a check is relaxed).
"""

import json
import re
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from neareast_api.core.config import Settings
from neareast_api.main import create_app

URL = "/api/v1/chat/messages"
ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = json.loads(
    (Path(__file__).parent / "chat_scenarios" / "scenarios.json").read_text("utf-8")
)["scenarios"]
PLACE_IDS = {p["id"] for p in json.loads((ROOT / "data" / "places.json").read_text("utf-8"))["places"]}
# Same idea as service.TIME_CLAIM: clock times, "N시", train counts, durations.
TIME_CLAIM = re.compile(r"\d{1,2}:\d{2}|\d{1,2}\s*시(?!즌)|\d+\s*편|\d+\s*분")
POLITE_ENDINGS = ("요", "죠")


def impolite_sentences(reply):
    """해요체 check: every sentence ends in 요/죠 (parentheses and tildes ignored)."""
    text = re.sub(r"\([^)]*\)", "", reply).replace("~", "")
    sentences = [part.strip() for part in re.split(r"[.!?…]+", text)]
    return [part for part in sentences if part and not part.endswith(POLITE_ENDINGS)]


def invariant_failures(request, body):
    """Rules every answer must keep (CLAUDE.md §7 Q2-Q4)."""
    failures = []
    context = request.get("context", {})
    dismissed, pins = set(context.get("dismissed", [])), set(context.get("pins", []))
    if len(body["chips"]) > 3:
        failures.append("Q2: more than 3 chips")
    for proposal in body["proposals"]:
        if proposal["needs_confirm"] is not True:
            failures.append("Q3: proposal without confirmation")
        if proposal["target"] in dismissed:
            failures.append(f"Q3: dismissed {proposal['target']} proposed again")
        if proposal["target"] not in PLACE_IDS:
            failures.append(f"Q3: proposal for unknown place {proposal['target']}")
        if proposal["type"] == "add" and proposal["target"] in pins:
            failures.append(f"Q3: proposes adding pinned {proposal['target']}")
    if TIME_CLAIM.search(body["reply"]) and body["tool_used"] is None:
        failures.append(f"Q4: time claim without tool: {body['reply']}")
    if body["intent"] == "knowledge" and not body["sources"] and "확인된 정보가 없어요" not in body["reply"]:
        failures.append("Q4: knowledge answer without sources")
    return failures


def run_scenario(client, scenario):
    """Returns (failures, transcript); transcript items are (message, body, seconds)."""
    expect = scenario["expect"]
    failures, transcript = [], []

    def ask(request):
        started = time.perf_counter()
        response = client.post(URL, json=request)
        elapsed = time.perf_counter() - started
        if response.status_code != 200:
            raise AssertionError(f"HTTP {response.status_code}: {response.text}")
        body = response.json()
        transcript.append((request["message"], body, elapsed))
        failures.extend(invariant_failures(request, body))
        return body

    try:
        body = ask(scenario["request"])
        for index in scenario.get("chain", []):
            if len(body["chips"]) <= index:
                failures.append(f"Q2: no chip #{index} to continue")
                break
            body = ask({"message": body["chips"][index], "context": scenario["request"]["context"]})
            if expect.get("each_step_has_chips"):
                if not body["chips"]:
                    failures.append("Q2: chain dead-ends without chips")
                if "확인된 정보가 없어요" in body["reply"]:
                    failures.append("Q2: chip led nowhere")
    except AssertionError as exc:
        return [str(exc)], transcript

    if "intent" in expect and body["intent"] != expect["intent"]:
        failures.append(f"intent {body['intent']} != {expect['intent']}")
    if "tool_used" in expect and body["tool_used"] != expect["tool_used"]:
        failures.append(f"tool_used {body['tool_used']} != {expect['tool_used']}")
    if "tool_used_in" in expect and body["tool_used"] not in expect["tool_used_in"]:
        failures.append(f"tool_used {body['tool_used']} not in {expect['tool_used_in']}")
    if "sources" in expect and body["sources"] != expect["sources"]:
        failures.append(f"sources {body['sources']} != {expect['sources']}")
    for source in expect.get("sources_include", []):
        if source not in body["sources"]:
            failures.append(f"sources {body['sources']} lack {source}")
    if "sources_include_any" in expect and not set(expect["sources_include_any"]) & set(body["sources"]):
        failures.append(f"sources {body['sources']} lack any of {expect['sources_include_any']}")
    for text in expect.get("reply_contains", []):
        if text not in body["reply"]:
            failures.append(f"reply lacks {text!r}")
    if "reply_contains_any" in expect and not any(t in body["reply"] for t in expect["reply_contains_any"]):
        failures.append(f"reply lacks any of {expect['reply_contains_any']}")
    for text in expect.get("reply_not_contains", []):
        if text in body["reply"]:
            failures.append(f"reply should not contain {text!r}")
    if expect.get("polite"):
        for _, step, _ in transcript:
            for sentence in impolite_sentences(step["reply"]):
                failures.append(f"not 해요체: {sentence!r}")
    if expect.get("no_proposals") and body["proposals"]:
        failures.append(f"expected no proposals, got {body['proposals']}")
    targets = {p["target"] for p in body["proposals"]}
    for target in expect.get("proposal_targets_exclude", []):
        if target in targets:
            failures.append(f"proposed excluded {target}")
    if "proposal_reason_contains" in expect:
        if not body["proposals"]:
            failures.append("expected a proposal")
        for proposal in body["proposals"]:
            for text in expect["proposal_reason_contains"]:
                if text not in proposal["reason"]:
                    failures.append(f"reason lacks {text!r}: {proposal['reason']}")
    if expect.get("proposal_certainty_not_null"):
        if not body["proposals"] or any(p["certainty"] is None for p in body["proposals"]):
            failures.append(f"expected time-bound proposals with certainty: {body['proposals']}")
    return failures, transcript


class ChatScenarioTests(unittest.TestCase):
    def test_targets_from_claude_md_section_7(self):
        counts = {}
        for scenario in SCENARIOS:
            counts[scenario["group"]] = counts.get(scenario["group"], 0) + 1
        for group, minimum in {"Q1": 10, "Q3": 10, "Q4": 20, "Q5": 5, "Q6": 5, "P": 5}.items():
            self.assertGreaterEqual(counts.get(group, 0), minimum, group)
        self.assertEqual(len({s["id"] for s in SCENARIOS}), len(SCENARIOS), "duplicate ids")

    def test_scenarios_in_mock_mode(self):
        client = TestClient(create_app(Settings(chat_mock=True)))
        for scenario in SCENARIOS:
            if scenario.get("llm_only"):
                continue
            with self.subTest(scenario["id"]):
                failures, transcript = run_scenario(client, scenario)
                self.assertEqual(failures, [], transcript[-1][:2] if transcript else None)


if __name__ == "__main__":
    unittest.main()
