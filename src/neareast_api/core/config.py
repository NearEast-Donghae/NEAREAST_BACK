"""Validated process settings, evaluated when an app is created."""

import os

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    docs_enabled: bool = True
    db_host: str = Field(default="127.0.0.1", min_length=1)
    db_port: int = Field(default=5432, ge=1, le=65535)
    db_name: str = Field(default="neareast", min_length=1)
    db_user: str = Field(default="neareast", min_length=1)
    db_password: SecretStr = SecretStr("")
    # Fixed chat answers without an LLM, so tests and frontend work without keys.
    chat_mock: bool = True
    # Folder holding knowledge/ and data/ (relative to the working directory).
    content_dir: str = Field(default=".", min_length=1)

    @classmethod
    def from_env(cls) -> "Settings":
        # Uvicorn --env-file or the container runtime loads .env values.
        # Never search parent directories or silently load a developer's file.
        values = {}
        for name in cls.model_fields:
            value = os.environ.get(f"NEAREAST_{name.upper()}")
            if value is not None:
                values[name] = value
        return cls.model_validate(values)
