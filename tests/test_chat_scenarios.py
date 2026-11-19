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
from legacy_support import create_app
from legacy_support import setUpModule as setUpModule
from legacy_support import tearDownModule as tearDownModule
from sqlalchemy import select
from storage_support import createStorageApp

from neareast_api.core.config import Settings
from neareast_api.db.entities import PlaceEntity
from neareast_api.modules.chat import service

URL = "/api/v1/chat/messages"
ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = json.loads(
    (Path(__file__).parent / "chat_scenarios" / "scenarios.json").read_text("utf-8")
)["scenarios"]


_catalogApp = createStorageApp()
with _catalogApp.state.sessionFactory() as _catalogSession:
    PLACE_IDS = set(_catalogSession.scalars(select(PlaceEntity.id)))
_catalogApp.state.databaseEngine.dispose()
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
    # Same parser as the server: a clock time needs a tool result or a cited document.
    if service._clock_claims(body["reply"]) and body["tool_used"] is None and not body["sources"]:
        failures.append(f"Q4: time claim without tool or source: {body['reply']}")
    if body["intent"] == "knowledge" and not body["sources"] and "확인된 정보가 없어요" not in body["reply"]:
        failures.append("Q4: knowledge answer without sources")
    return failures


def run_scenario(client, scenario, mode=None):
    """Returns (failures, transcript); transcript items are (message, body, seconds).
    `mode` (e.g. "llm") fails answers that came from another responder, such as a fallback."""
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
        if mode is not None and body["mode"] != mode:
            failures.append(f"answered in {body['mode']} mode, expected {mode}")
        failures.extend(invariant_failures(request, body))
        return body

    try:
        first = scenario["request"]
        history = list(first.get("history", []))
        body = ask(first)
        for index in scenario.get("chain", []):
            if len(body["chips"]) <= index:
                failures.append(f"Q2: no chip #{index} to continue")
                break
            # Like the real client: tapping a chip sends it with the conversation so far.
            history += [{"role": "user", "content": transcript[-1][0]},
                        {"role": "assistant", "content": body["reply"]}]
            body = ask({"message": body["chips"][index], "context": first["context"],
                        "history": list(history)})
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
        targets = {"Q1": 10, "Q3": 10, "Q4": 20, "Q5": 5, "Q6": 5, "P": 5, "M": 5, "V": 10}
        for group, minimum in targets.items():
            self.assertGreaterEqual(counts.get(group, 0), minimum, group)
        self.assertEqual(len({s["id"] for s in SCENARIOS}), len(SCENARIOS), "duplicate ids")

    def test_scenarios_in_mock_mode(self):
        client = TestClient(create_app(Settings(chat_mock=True, chat_rate_limit_per_minute=0)))
        for scenario in SCENARIOS:
            if scenario.get("llm_only"):
                continue
            with self.subTest(scenario["id"]):
                failures, transcript = run_scenario(client, scenario)
                self.assertEqual(failures, [], transcript[-1][:2] if transcript else None)


if __name__ == "__main__":
    unittest.main()
