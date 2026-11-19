"""Validated process settings, evaluated when an app is created."""

import os
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tmapApiKey: SecretStr = SecretStr("")
    docs_enabled: bool = True
    sessionCookieName: str = "neareast_session"
    sessionLifetimeHours: int = Field(default=168, ge=1, le=720)
    cookieSecure: bool = False
    # Browser origins allowed to call the API, comma-separated. Empty: no cross-origin access.
    cors_allow_origins: str = ""
    db_host: str = Field(default="127.0.0.1", min_length=1)
    db_port: int = Field(default=5432, ge=1, le=65535)
    db_name: str = Field(default="neareast", min_length=1)
    db_user: str = Field(default="neareast", min_length=1)
    db_password: SecretStr = SecretStr("")
    # Fixed chat answers without an LLM, so tests and frontend work without keys.
    chat_mock: bool = True
    # Chat requests allowed per client address per minute (0 = no limit).
    chat_rate_limit_per_minute: int = Field(default=60, ge=0, le=100000)
    # Legacy PR configuration retained for call compatibility; content is now stored in PostgreSQL.
    content_dir: str = Field(default=".", min_length=1)
    # LLM (used only when chat_mock is false). The key lives in .env only.
    openai_api_key: SecretStr = SecretStr("")
    chat_model: str = Field(default="gpt-5.4-nano", min_length=1)
    embedding_model: str = Field(default="text-embedding-3-large", min_length=1)
    # Vector search (retrieval step 3) on or off; steps 1 and 2 always run.
    chat_vector_search: bool = True
    # A document less similar to the question than this is not used as evidence.
    vector_min_similarity: float = Field(default=0.27, ge=0, le=1)
    # The question embedding may take this long; past it the answer uses steps 1 and 2 only.
    embedding_timeout_seconds: float = Field(default=2, gt=0, le=30)
    chat_max_tokens: int = Field(default=1000, ge=50, le=4000)
    # Only for reasoning models (gpt-5 family); unset sends no reasoning parameter.
    chat_reasoning_effort: Literal["none", "minimal", "low", "medium", "high"] | None = None
    llm_timeout_seconds: float = Field(default=8, gt=0, le=120)
    # Whole-request budget for the model path; past it the built-in responder answers.
    chat_deadline_seconds: float = Field(default=12, gt=0, le=120)
    # Every LLM/embedding call appends its token usage here (JSON lines).
    llm_usage_log: str = Field(default="llm_usage.jsonl", min_length=1)

    @classmethod
    def from_env(cls) -> "Settings":
        # Uvicorn --env-file or the container runtime loads .env values.
        # Never search parent directories or silently load a developer's file.
        values = {}
        for name in cls.model_fields:
            envName = re.sub(r"(?<!^)(?=[A-Z])", "_", name).upper()
            value = os.environ.get(f"NEAREAST_{envName}")
            if value is not None:
                values[name] = value
        if not values.get("openai_api_key") and os.environ.get("OPENAI_API_KEY"):
            values["openai_api_key"] = os.environ["OPENAI_API_KEY"]
        if not values.get("tmapApiKey") and os.environ.get("TMAP_API_KEY"):
            values["tmapApiKey"] = os.environ["TMAP_API_KEY"]
        return cls.model_validate(values)
