"""Load and validate config.json. The bot refuses to start if it is invalid."""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RateLimit(_Strict):
    per_user_per_minute: int = Field(ge=1)
    per_channel_per_minute: int = Field(ge=1)


class DMForwarding(_Strict):
    enabled: bool


class Unprompted(_Strict):
    name_mention: bool
    random_chance: float = Field(ge=0.0, le=1.0)
    channel_cooldown_seconds: int = Field(ge=0)
    default_keywords: list[str]


class Config(_Strict):
    owner_ids: list[int] = Field(min_length=1)
    model: str = Field(min_length=1)
    max_output_tokens: int = Field(ge=1, le=4096)
    history_window: int = Field(ge=1)
    summary_trigger: int = Field(ge=1)
    rate_limit: RateLimit
    dm_forwarding: DMForwarding
    moderator_role_names: list[str]
    unprompted: Unprompted
    default_quiet_channel_words: list[str]


class Secrets(_Strict):
    discord_token: str = Field(min_length=1)
    anthropic_api_key: str = Field(min_length=1)


class ConfigError(Exception):
    pass


def load_config(path: str | Path) -> Config:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        cfg = Config.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValidationError) as e:
        raise ConfigError(f"Invalid config {path}: {e}") from e
    if cfg.summary_trigger <= cfg.history_window:
        raise ConfigError("summary_trigger must be greater than history_window")
    return cfg


def load_secrets() -> Secrets:
    try:
        return Secrets(
            discord_token=os.environ.get("DISCORD_TOKEN", ""),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        )
    except ValidationError as e:
        raise ConfigError("DISCORD_TOKEN and ANTHROPIC_API_KEY must be set") from e
