"""DM logging, forwarding to bot owners, and owner replies sent through the bot."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from harmony.bot import HarmonyBot

log = logging.getLogger(__name__)
NO_MENTIONS = discord.AllowedMentions.none()


def _embed(user_tag: str, user_id: int, content: str, attachments: list[str], when: datetime | None) -> discord.Embed:
    e = discord.Embed(title=f"DM from {user_tag} ({user_id})", description=content[:4000] or "*(no text)*", timestamp=when)
    if attachments:
        e.add_field(name="Attachments", value="\n".join(attachments)[:1024], inline=False)
    return e


class DMForwarder:
    def __init__(self, bot: "HarmonyBot"):
        self.bot = bot

    async def _send_to_owners(self, author_id: int, embed: discord.Embed) -> bool:
        ok = True
        for owner_id in self.bot.cfg.owner_ids:
            if owner_id == author_id:
                continue  # owners' own DMs are not forwarded to themselves
            try:
                owner = self.bot.get_user(owner_id) or await self.bot.fetch_user(owner_id)
                await owner.send(embed=embed, allowed_mentions=NO_MENTIONS)
            except discord.HTTPException as e:
                log.warning("Could not forward DM to owner %s: %s", owner_id, e)
                ok = False
        return ok

    async def forward(self, message: discord.Message) -> None:
        author = message.author
        attachments = [a.url for a in message.attachments]
        dm_id = await self.bot.store.log_dm(author.id, "in", message.content, attachments, forwarded=False)
        embed = _embed(str(author), author.id, message.content, attachments, message.created_at)
        if await self._send_to_owners(author.id, embed):
            await self.bot.store.set_dm_forwarded(dm_id, True)

    async def retry_pending(self) -> None:
        for row in await self.bot.store.unforwarded_dms():
            when = datetime.fromisoformat(row["created_at"])
            embed = _embed(f"user {row['user_id']}", row["user_id"], row["content"], row["attachments"], when)
            if await self._send_to_owners(row["user_id"], embed):
                await self.bot.store.set_dm_forwarded(row["id"], True)

    async def owner_reply(self, target: discord.User, text: str) -> None:
        """Sent exactly as written; never goes through the model. Raises discord.HTTPException."""
        channel = target.dm_channel or await target.create_dm()
        await channel.send(text, allowed_mentions=NO_MENTIONS)
        await self.bot.store.log_dm(target.id, "owner_reply", text, [], forwarded=True)
        await self.bot.store.add_message(channel.id, None, self.bot.user.id, "assistant", text, source="owner")
