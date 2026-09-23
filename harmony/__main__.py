"""Entry point: python -m harmony"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from harmony.ai.client import AIClient
from harmony.bot import HarmonyBot
from harmony.config import Config, ConfigError, Secrets, load_config, load_secrets
from harmony.memory.db import connect
from harmony.memory.store import Store

log = logging.getLogger("harmony")


async def run(cfg: Config, secrets: Secrets, config_path: Path, persona_dir: Path, db_path: str) -> None:
    db = await connect(db_path)
    try:
        ai = AIClient(secrets.anthropic_api_key, cfg.model, cfg.max_output_tokens)
        bot = HarmonyBot(cfg, ai, Store(db), config_path, persona_dir)
        for name, digest in bot.file_hashes().items():
            log.info("SHA-256 %s = %s", name, digest)
        async with bot:
            await bot.start(secrets.discord_token)
    finally:
        await db.close()


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    config_path = Path(os.environ.get("HARMONY_CONFIG", "config.json"))
    persona_dir = Path(os.environ.get("HARMONY_PERSONA_DIR", "persona"))
    db_path = os.environ.get("HARMONY_DB", "harmony.db")
    try:
        cfg = load_config(config_path)
        secrets = load_secrets()
        for f in ("harmony.md", "rules.md"):
            if not (persona_dir / f).is_file():
                raise ConfigError(f"Missing persona file {persona_dir / f}")
    except ConfigError as e:
        log.critical("%s", e)
        sys.exit(1)
    asyncio.run(run(cfg, secrets, config_path, persona_dir, db_path))


if __name__ == "__main__":
    main()
