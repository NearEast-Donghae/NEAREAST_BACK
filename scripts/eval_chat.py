"""Run tests/chat_scenarios against the REAL LLM; report pass rates, latency, tokens, cost, cache.

This spends API tokens; run it only for evaluation (e.g. after a prompt change).
The key is read from the env file by this process and never printed.

    python scripts/eval_chat.py --quiet                          # all scenarios, NEAREAST_CHAT_MODEL
    python scripts/eval_chat.py --model gpt-5.4-nano --reasoning-effort low --quiet
    python scripts/eval_chat.py --rerun-failed                   # what failed last time (same model)
    python scripts/eval_chat.py --group Q5
    python scripts/eval_chat.py --only q5-track --out result.json
"""

import argparse
import datetime as dt
import importlib.util
import json
import statistics
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
from fastapi.testclient import TestClient

from neareast_api.core.config import Settings
from neareast_api.main import create_app

ROOT = Path(__file__).resolve().parents[1]
LAST_RUN = ROOT / "eval_last.json"  # failed scenario ids per model (git-ignored)

# USD per 1M tokens: (input, cached input, output). Check the current price page before quoting.
PRICES = {
    "gpt-5.4-nano": (0.20, 0.02, 1.25),  # OpenAI price list, given 2026-11-19
    "gpt-4.1-mini": (0.40, 0.10, 1.60),  # OpenAI pricing page, checked 2026-11-19
    "gpt-4.1": (2.00, 0.50, 8.00),
    "text-embedding-3-small": (0.02, 0.02, 0.0),
}


def load_scenario_module():
    sys.path.insert(0, str(ROOT / "tests"))
    spec = importlib.util.spec_from_file_location(
        "chat_scenarios", ROOT / "tests" / "test_chat_scenarios.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def estimate_cost(entries: list[dict]) -> float | None:
    total = 0.0
    for e in entries:
        price = PRICES.get(e["model"])
        if price is None:
            return None
        cached = e.get("cached_tokens") or 0
        uncached = (e.get("prompt_tokens") or 0) - cached
        total += (uncached * price[0] + cached * price[1]
                  + (e.get("completion_tokens") or 0) * price[2]) / 1_000_000
    return total


def read_last() -> dict:
    try:
        return json.loads(LAST_RUN.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def summarize(entries: list[dict], latencies: list[float]) -> dict:
    prompt = sum(e.get("prompt_tokens") or 0 for e in entries)
    cached = sum(e.get("cached_tokens") or 0 for e in entries)
    return {
        "api_calls": len(entries),
        "after_tool_calls": sum(1 for e in entries if e["kind"] == "chat:after_tool"),
        "prompt_tokens": prompt,
        "cached_tokens": cached,
        "cache_ratio": round(cached / prompt, 3) if prompt else None,
        "completion_tokens": sum(e.get("completion_tokens") or 0 for e in entries),
        "cost_usd": estimate_cost(entries),
        "requests": len(latencies),
        "latency_avg_s": round(statistics.mean(latencies), 2) if latencies else None,
        "latency_p50_s": round(statistics.median(latencies), 2) if latencies else None,
        "latency_max_s": round(max(latencies), 2) if latencies else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate chat scenarios with the real LLM")
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    parser.add_argument("--model", help="chat model to evaluate (default: settings)")
    parser.add_argument("--reasoning-effort",
                        choices=["none", "minimal", "low", "medium", "high"],
                        help="reasoning models only")
    parser.add_argument("--rerun-failed", action="store_true",
                        help="only scenarios that failed in the last run of this model/effort")
    parser.add_argument("--group", help="Q1..Q6 or P")
    parser.add_argument("--only", nargs="*", default=[])
    parser.add_argument("--quiet", action="store_true", help="hide transcripts of passing scenarios")
    parser.add_argument("--out", help="write the run summary as JSON here")
    args = parser.parse_args(argv)

    load_dotenv(args.env_file, override=False)
    run_log = Path(tempfile.mkstemp(prefix="eval_usage_", suffix=".jsonl")[1])
    update = {"chat_mock": False, "llm_usage_log": str(run_log), "chat_rate_limit_per_minute": 0}
    if args.model:
        update["chat_model"] = args.model
    if args.reasoning_effort:
        update["chat_reasoning_effort"] = args.reasoning_effort
    base = Settings.from_env()
    settings = base.model_copy(update=update)
    if not settings.openai_api_key.get_secret_value():
        print(f"NEAREAST_OPENAI_API_KEY가 없습니다 ({args.env_file}).", file=sys.stderr)
        return 2
    label = settings.chat_model + (
        f"@{settings.chat_reasoning_effort}" if settings.chat_reasoning_effort else ""
    )

    module = load_scenario_module()
    only = set(args.only)
    if args.rerun_failed:
        previous = read_last().get(label)
        if previous is None:
            print(f"{label}의 이전 실행 기록이 없습니다.", file=sys.stderr)
            return 2
        only |= set(previous["failed"])
        if not only:
            print(f"{label}: 지난 실행에서 실패한 시나리오가 없습니다.")
            return 0
    scenarios = [
        s for s in module.SCENARIOS
        if (not args.group or s["group"] == args.group) and (not only or s["id"] in only)
    ]
    from uuid import uuid4
    from sqlalchemy import create_engine, delete
    from sqlalchemy.orm import Session
    from neareast_api.db.entities import UserEntity
    from neareast_api.db.url import build_database_url

    client = TestClient(create_app(settings))
    response = client.post("/api/v1/auth/signup", json={
        "email": f"eval-{uuid4().hex}@example.com", "nickname": "LLM evaluation", "password": uuid4().hex,
    })
    response.raise_for_status()
    evaluationUserId = response.json()["user"]["id"]
    client.headers["X-CSRF-Token"] = response.json()["csrfToken"]

    def removeEvaluationUser():
        engine = create_engine(build_database_url(settings))
        try:
            with Session(engine) as session:
                session.execute(delete(UserEntity).where(UserEntity.id == evaluationUserId))
                session.commit()
        finally:
            engine.dispose()
    import atexit
    atexit.register(removeEvaluationUser)

    by_group: dict[str, list[bool]] = defaultdict(list)
    failed: dict[str, list[str]] = {}
    latencies: list[float] = []
    print(f"== {label} · {len(scenarios)} scenarios ==")
    for scenario in scenarios:
        # mode="llm": a silent fallback answer must not count as a model pass
        failures, transcript = module.run_scenario(client, scenario, mode="llm")
        latencies += [seconds for _, _, seconds in transcript]
        by_group[scenario["group"]].append(not failures)
        if failures:
            failed[scenario["id"]] = failures
        print(f"[{'FAIL' if failures else 'PASS'}] {scenario['group']} {scenario['id']}")
        if failures or not args.quiet:
            for message, body, seconds in transcript:
                print(f"    > {message}  ({seconds:.1f}s)")
                print(f"      {body['intent']} | {body['reply']}")
                print(f"      chips={body['chips']} sources={body['sources']} "
                      f"tool={body['tool_used']} "
                      f"proposals={[(p['target'], p['certainty']) for p in body['proposals']]}")
        for failure in failures:
            print(f"    ! {failure}")

    records = [json.loads(line) for line in run_log.read_text("utf-8").splitlines() if line.strip()]
    if records:  # keep the shared usage record complete
        with Path(base.llm_usage_log).open("a", encoding="utf-8") as shared:
            shared.writelines(json.dumps(e, ensure_ascii=False) + "\n" for e in records)
    entries = [e for e in records if e["kind"] != "request"]  # model calls (tokens)
    slowest = max((e for e in records if e["kind"] == "request"),
                  key=lambda e: e["total_ms"], default=None)
    run_log.unlink(missing_ok=True)
    stats = summarize(entries, latencies)
    groups = {g: [sum(r), len(r)] for g, r in sorted(by_group.items())}
    passed = sum(p for p, _ in groups.values())

    print("\n-- pass rate by group --")
    for group, (ok, total) in groups.items():
        print(f"  {group}: {ok}/{total}")
    cost = f"${stats['cost_usd']:.4f}" if stats["cost_usd"] is not None else "unknown price"
    print(f"\n{label}: {passed}/{len(scenarios)} passed · {stats['api_calls']} API calls · "
          f"prompt {stats['prompt_tokens']} (cached {stats['cached_tokens']}, "
          f"{(stats['cache_ratio'] or 0):.0%}) · completion {stats['completion_tokens']} · {cost} · "
          f"latency avg {stats['latency_avg_s']}s / max {stats['latency_max_s']}s")

    if slowest:
        print(f"slowest request: {slowest['total_ms']} ms, stages {slowest['stages']}")
    if args.out:
        Path(args.out).write_text(json.dumps({
            "label": label, "at": dt.datetime.now().isoformat(timespec="seconds"),
            "passed": passed, "total": len(scenarios), "groups": groups,
            "failed": failed, **stats,
        }, ensure_ascii=False, indent=2), "utf-8")
    if not args.only and not args.group:  # only full or rerun runs update the record
        last = read_last()
        ids = list(failed)
        if args.rerun_failed:
            ids = [i for i in last[label]["failed"] if i in failed]
        last[label] = {"at": dt.datetime.now().isoformat(timespec="seconds"), "failed": ids}
        LAST_RUN.write_text(json.dumps(last, ensure_ascii=False, indent=2), "utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
