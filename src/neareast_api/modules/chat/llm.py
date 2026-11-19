"""Thin OpenAI boundary: lazy client, structured JSON output, token usage records.

Synchronous on purpose: chat handlers are `def`, so FastAPI runs them in its
thread pool. Every call appends its usage to settings.llm_usage_log (JSON lines)
and logs it, so spending can be summed per day. Errors become LLMError; the
service turns that into 503.
"""

import datetime as dt
import json
import logging
import threading
import time
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

from neareast_api.core.config import Settings

logger = logging.getLogger(__name__)
_usage_lock = threading.Lock()


class LLMError(Exception):
    """The LLM could not produce a usable answer (no key, network, bad output)."""


class Deadline:
    """Time budget for one chat request; every model call fits inside what is left."""

    def __init__(self, seconds: float):
        self._end = time.monotonic() + seconds

    def remaining(self) -> float:
        return self._end - time.monotonic()


MIN_CALL_SECONDS = 0.5  # not worth starting a call with less than this left


def _describe(exc: Exception, model: str) -> str:
    """Enough to tell 401 from 429 from a wrong model name on the day, without the
    provider's message text (it can echo request details)."""
    parts = [type(exc).__name__]
    for name in ("status_code", "code", "request_id"):
        value = getattr(exc, name, None)
        if value:
            parts.append(f"{name}={value}")
    parts.append(f"model={model}")
    return " ".join(parts)


@lru_cache(maxsize=2)
def _client(api_key: str, timeout: float):
    from openai import OpenAI  # imported on first use, never at module import

    return OpenAI(api_key=api_key, timeout=timeout, max_retries=0)


def _client_for(settings: Settings):
    key = settings.openai_api_key.get_secret_value()
    if not key:
        raise LLMError("NEAREAST_OPENAI_API_KEY is not set")
    return _client(key, settings.llm_timeout_seconds)


def record_usage(
    settings: Settings, kind: str, model: str, usage: Any, latency: float | None = None
) -> dict:
    entry = {
        "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "kind": kind,
        "model": model,
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
        # Prompt tokens served from OpenAI's automatic prompt cache (cheaper).
        "cached_tokens": getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", None),
        "latency_ms": round(latency * 1000) if latency is not None else None,
    }
    logger.info("llm usage %s", entry)
    try:
        with _usage_lock, Path(settings.llm_usage_log).open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        logger.warning("cannot write llm usage log %s", settings.llm_usage_log)
    return entry


def _call_timeout(settings: Settings, deadline: Deadline | None, label: str) -> float:
    if deadline is None:
        return settings.llm_timeout_seconds
    remaining = deadline.remaining()
    if remaining < MIN_CALL_SECONDS:
        raise LLMError(f"request deadline reached before {label}")
    return min(settings.llm_timeout_seconds, remaining)


def _retryable(exc: Exception) -> bool:
    """Timeouts, dropped connections, rate limits and server errors; not 4xx mistakes."""
    status = getattr(exc, "status_code", None)
    return status is None or status == 429 or status >= 500


def chat_json(
    settings: Settings, messages: list[dict], schema: dict, label: str,
    deadline: Deadline | None = None,
) -> dict:
    """One chat completion constrained to `schema`; returns the parsed object.
    Retries once on a transient failure, always within the request deadline."""
    from openai import OpenAIError

    client = _client_for(settings)
    options = {}
    if settings.chat_reasoning_effort is not None:
        options["reasoning_effort"] = settings.chat_reasoning_effort
    started = time.perf_counter()
    for attempt in (1, 2):
        timeout = _call_timeout(settings, deadline, label)
        try:
            # The SDK's own retries are off: they would not respect the deadline.
            response = client.with_options(timeout=timeout, max_retries=0).chat.completions.create(
                model=settings.chat_model,
                messages=messages,
                max_completion_tokens=settings.chat_max_tokens,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "chat_draft", "strict": True, "schema": schema},
                },
                **options,
            )
            break
        except OpenAIError as exc:
            reason = _describe(exc, settings.chat_model)
            if attempt == 2 or not _retryable(exc):
                raise LLMError(f"chat call failed: {reason}") from exc
            logger.warning("chat call attempt 1 failed, retrying: %s", reason)
    record_usage(settings, f"chat:{label}", settings.chat_model, response.usage,
                 time.perf_counter() - started)
    choice = response.choices[0]
    if choice.finish_reason == "length":
        raise LLMError("answer cut off by max tokens")
    try:
        return json.loads(choice.message.content or "")
    except ValueError as exc:
        raise LLMError("answer was not valid JSON") from exc


def record_request(settings: Settings, entry: dict) -> None:
    """One line per chat request with per-stage timings, next to the token records."""
    entry = {"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
             "kind": "request", **entry}
    try:
        with _usage_lock, Path(settings.llm_usage_log).open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        logger.warning("cannot write llm usage log %s", settings.llm_usage_log)


def embed_query(
    settings: Settings, text: str, deadline: Deadline | None = None
) -> tuple[str, Sequence[float]]:
    from openai import OpenAIError

    client = _client_for(settings)
    timeout = _call_timeout(settings, deadline, "embedding")
    try:
        response = client.with_options(timeout=timeout, max_retries=0).embeddings.create(
            model=settings.embedding_model, input=[text]
        )
    except OpenAIError as exc:
        raise LLMError(
            f"embedding call failed: {_describe(exc, settings.embedding_model)}"
        ) from exc
    record_usage(settings, "embedding:query", settings.embedding_model, response.usage)
    return settings.embedding_model, response.data[0].embedding
