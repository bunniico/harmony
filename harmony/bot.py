"""Discord client and event wiring."""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from pathlib import Path

import discord
from discord import app_commands

from harmony import permissions
from harmony.ai.client import AIClient
from harmony.ai.prompt import Persona, build_stable_prompt
from harmony.config import Config
from harmony.features import commands
from harmony.features.chat import ChatHandler
from harmony.features.dm_forward import DMForwarder
from harmony.memory.store import Store
from harmony.security.output_guard import OutputGuard

log = logging.getLogger(__name__)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class HarmonyBot(discord.Client):
    def __init__(self, cfg: Config, ai: AIClient, store: Store, config_path: Path, persona_dir: Path):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.tree = app_commands.CommandTree(self)
        self.cfg, self.ai, self.store = cfg, ai, store
        self.config_path, self.persona_dir = config_path, persona_dir
        self.canary = "HARMONY-CANARY-" + secrets.token_hex(12)
        self.started = time.monotonic()
        self.keywords: list[str] = []
        self.reload_persona()
        self.chat = ChatHandler(self)
        self.dm = DMForwarder(self)

    def reload_persona(self) -> None:
        persona = Persona.load(self.persona_dir)
        self.persona = persona
        self.stable_prompt = build_stable_prompt(persona, self.canary)
        self.guard = OutputGuard(self.canary, persona.rules)

    def file_hashes(self) -> dict[str, str]:
        files = [self.config_path, self.persona_dir / "harmony.md", self.persona_dir / "rules.md"]
        return {p.name: sha256_file(p) for p in files}

    async def set_keyword(self, word: str, present: bool) -> None:
        await self.store.set_keyword(word, present)
        self.keywords = await self.store.keywords()

    async def ensure_guild(self, guild: discord.Guild) -> None:
        mod_roles = [r.id for r in guild.roles if r.name in self.cfg.moderator_role_names]
        if await self.store.ensure_guild(guild.id, mod_roles, self.cfg.default_quiet_channel_words):
            log.info("Seeded settings for guild %s", guild.id)

    async def level_for(self, user: discord.abc.User, guild: discord.Guild | None) -> permissions.Level:
        mod_roles: list[int] = []
        if guild is not None:
            await self.ensure_guild(guild)
            mod_roles = (await self.store.get_guild(guild.id)).mod_role_ids
        return permissions.level_for(user, guild, self.cfg.owner_ids, mod_roles)

    async def setup_hook(self) -> None:
        await self.store.seed_keywords_once(self.cfg.unprompted.default_keywords)
        self.keywords = await self.store.keywords()
        commands.register(self.tree)
        await self.tree.sync()

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s) in %d guild(s)", self.user, self.user.id, len(self.guilds))
        for g in self.guilds:
            await self.ensure_guild(g)
        await self.dm.retry_pending()

    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self.ensure_guild(guild)

    async def on_message(self, message: discord.Message) -> None:
        try:
            await self.chat.handle(message)
        except Exception:
            log.exception("Error handling message %s", message.id)
