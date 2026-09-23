"""Main chat handler: the guild/DM message flow from PLAN.md section 9.4."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

import discord

from harmony.ai.client import AIUnavailable
from harmony.ai.prompt import build_messages, build_volatile_context, wrap_user_message
from harmony.ai.summarize import extract_facts, summarize
from harmony.features.triggers import UnpromptedTrigger, is_quiet_channel, should_respond
from harmony.permissions import Level
from harmony.security.detect import StrikeTracker, detect_injection
from harmony.security.ratelimit import RateLimiter
from harmony.security.sanitize import clean

if TYPE_CHECKING:
    from harmony.bot import HarmonyBot

log = logging.getLogger(__name__)

FACT_CAP = 30
SPACED_OUT = "...Huh? Sorry, I totally spaced out. What were we talking about?"
SLOW_DOWN = "Uhhh, slow down. I can only do, like, one thing at a time. Barely."
NOTICE_COOLDOWN = 60.0
NO_MENTIONS = discord.AllowedMentions.none()


class ChatHandler:
    def __init__(self, bot: "HarmonyBot"):
        self.bot = bot
        cfg = bot.cfg
        self.limiter = RateLimiter(cfg.rate_limit.per_user_per_minute, cfg.rate_limit.per_channel_per_minute)
        self.unprompted = UnpromptedTrigger(
            name_mention=cfg.unprompted.name_mention,
            random_chance=cfg.unprompted.random_chance,
            cooldown_seconds=cfg.unprompted.channel_cooldown_seconds,
        )
        self.strikes = StrikeTracker()
        self._notified: dict[int, float] = {}
        self._summary_locks: dict[int, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task] = set()

    async def _is_quiet(self, message: discord.Message) -> bool:
        if message.guild is None:
            return False
        store, chan = self.bot.store, message.channel
        ids, names = [chan.id], [getattr(chan, "name", "") or ""]
        parent = getattr(chan, "parent", None)
        if parent is not None:
            ids.append(parent.id)
            names.append(parent.name)
        return is_quiet_channel(
            ids, names, await store.quiet_channels(message.guild.id), await store.quiet_words(message.guild.id)
        )

    def _is_direct(self, message: discord.Message) -> bool:
        me = self.bot.user
        if message.guild is None or me in message.mentions:
            return True
        ref = message.reference.resolved if message.reference else None
        return isinstance(ref, discord.Message) and ref.author.id == me.id

    async def _channel_allowed(self, message: discord.Message) -> bool:
        if message.guild is None:
            return True
        s = await self.bot.store.get_guild(message.guild.id)
        parent_id = getattr(message.channel, "parent_id", None)
        return s.enabled and s.channel_enabled(message.channel.id) and (
            parent_id is None or s.channel_enabled(parent_id)
        )

    async def handle(self, message: discord.Message) -> None:
        bot, store, author = self.bot, self.bot.store, message.author
        if author.bot or message.webhook_id or author.id == bot.user.id:
            return
        if await store.is_blocked(author.id):
            return
        if message.guild is None and bot.cfg.dm_forwarding.enabled:
            await bot.dm.forward(message)
        if message.guild is not None:
            await bot.ensure_guild(message.guild)
        if await self._is_quiet(message):
            return  # no reply, no storage
        level = await bot.level_for(author, message.guild)
        is_owner = level == Level.BOTOWNER  # from config owner_ids, never from message text
        if not is_owner and self.strikes.in_cooldown(author.id):
            return

        raw = message.clean_content
        if message.attachments:
            raw += "".join(f" [attachment: {a.filename}]" for a in message.attachments)
        body = clean(raw)
        if not body:
            return
        flagged = not is_owner and detect_injection(raw)
        if flagged:
            log.warning("Injection heuristic hit from user %s in channel %s", author.id, message.channel.id)
            self.strikes.hit(author.id)

        chan_id = message.channel.id
        reason = should_respond(
            quiet=False,
            direct=self._is_direct(message),
            unprompted_reason=lambda: self.unprompted.reason(chan_id, raw, flagged, bot.keywords),
        )
        if reason is None or not await self._channel_allowed(message):
            return
        if not self.limiter.allow(author.id, chan_id):
            if reason == "direct":
                await self._slow_down_notice(message)
            return
        if reason != "direct":
            self.unprompted.mark(chan_id)

        guild_id = message.guild.id if message.guild else None
        content = wrap_user_message(author.id, author.display_name, level.label, body, flagged)
        msg_id = await store.add_message(chan_id, guild_id, author.id, "user", content, flagged)

        async with message.channel.typing():
            reply, ok = await self._generate(message)
        reply = bot.guard.check(reply)
        try:
            if message.guild is None:
                await message.channel.send(reply, allowed_mentions=NO_MENTIONS)
            else:
                await message.reply(reply, mention_author=False, allowed_mentions=NO_MENTIONS)
        except discord.HTTPException:
            log.exception("Failed to send reply in channel %s", chan_id)
            return
        if not ok:
            return
        await store.add_message(chan_id, guild_id, bot.user.id, "assistant", reply)
        self._spawn(self._memory_jobs(chan_id, author.id, body, msg_id, flagged))

    async def _generate(self, message: discord.Message) -> tuple[str, bool]:
        bot, store, chan = self.bot, self.bot.store, message.channel
        history = await store.recent_messages(chan.id, bot.cfg.history_window)
        summary, _ = await store.get_summary(chan.id)
        user_ids = list(dict.fromkeys(m.author_id for m in history if m.role == "user"))
        if message.guild is None:
            location = f"a private DM with {message.author.display_name}"
        else:
            location = f"the Discord server \"{message.guild.name}\", channel #{getattr(chan, 'name', '?')}"
        volatile = build_volatile_context(
            directive=await store.get_directive(),
            summary=summary,
            facts=await store.facts_for_users(user_ids),
            location=location,
        )
        try:
            return await bot.ai.chat(bot.stable_prompt, volatile, build_messages(history)), True
        except AIUnavailable:
            return SPACED_OUT, False

    async def _slow_down_notice(self, message: discord.Message) -> None:
        now = time.monotonic()
        if now - self._notified.get(message.author.id, -NOTICE_COOLDOWN) < NOTICE_COOLDOWN:
            return
        self._notified[message.author.id] = now
        try:
            await message.channel.send(SLOW_DOWN, allowed_mentions=NO_MENTIONS)
        except discord.HTTPException:
            pass

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _memory_jobs(self, chan_id: int, author_id: int, body: str, msg_id: int, flagged: bool) -> None:
        try:
            if not flagged:
                facts = await extract_facts(self.bot.ai, body)
                if facts:
                    await self.bot.store.add_facts(author_id, facts, msg_id, FACT_CAP)
            await self.maybe_summarize(chan_id)
        except AIUnavailable:
            pass
        except Exception:
            log.exception("Background memory job failed for channel %s", chan_id)

    async def maybe_summarize(self, chan_id: int) -> None:
        store, cfg = self.bot.store, self.bot.cfg
        async with self._summary_locks.setdefault(chan_id, asyncio.Lock()):
            old, last_id = await store.get_summary(chan_id)
            pending = await store.messages_after(chan_id, last_id)
            if len(pending) <= cfg.summary_trigger:
                return
            batch = pending[: -cfg.history_window]  # keep the verbatim window out of the summary
            new = await summarize(self.bot.ai, old, batch) if any(not m.flagged for m in batch) else old
            await store.set_summary(chan_id, new, batch[-1].id)
