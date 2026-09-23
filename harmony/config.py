"""Load and validate config.json. The bot refuses to start if it is invalid."""

from __future__ import annotations

import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


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


class Adderall(_Strict):
    """Link to an adderall to-do app. Only `user_id` can drive it, and that
    same person gets the alarm, action and digest DMs."""

    enabled: bool
    url: str = Field(min_length=1)
    user_id: int
    timezone: str = "UTC"
    digest_time: str | None = Field(default="08:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")  # null turns it off
    alarms: bool = True
    webhook_port: int | None = Field(default=None, ge=1, le=65535)  # null: don't receive adderall's webhooks

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"unknown timezone {v!r}") from e
        return v


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
    adderall: Adderall | None = None


class Secrets(_Strict):
    discord_token: str = Field(min_length=1)
    anthropic_api_key: str = Field(min_length=1)
    adderall_webhook_token: str = ""


MIN_WEBHOOK_TOKEN = 24


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
            adderall_webhook_token=os.environ.get("ADDERALL_WEBHOOK_TOKEN", ""),
        )
    except ValidationError as e:
        raise ConfigError("DISCORD_TOKEN and ANTHROPIC_API_KEY must be set") from e


def check_webhook_secret(cfg: Config, secrets: Secrets) -> None:
    """The webhook URL's token is its only lock, so refuse to open the port without a real one."""
    a = cfg.adderall
    if a and a.enabled and a.webhook_port and len(secrets.adderall_webhook_token) < MIN_WEBHOOK_TOKEN:
        raise ConfigError(
            f"adderall.webhook_port is set, so ADDERALL_WEBHOOK_TOKEN must be at least {MIN_WEBHOOK_TOKEN} "
            "characters (try: python -c \"import secrets; print(secrets.token_urlsafe(32))\")"
        )
