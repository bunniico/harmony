"""Permission levels, resolved from Discord data only. Never from message text."""

from __future__ import annotations

from enum import IntEnum
from typing import Iterable

import discord
from discord import app_commands


class Level(IntEnum):
    USER = 0
    SERVERMODERATOR = 1
    SERVEROWNER = 2
    BOTOWNER = 3

    @property
    def label(self) -> str:
        return self.name.lower()


def resolve_level(
    user_id: int,
    owner_ids: Iterable[int],
    *,
    guild_owner_id: int | None = None,
    manage_guild: bool = False,
    manage_messages: bool = False,
    role_ids: Iterable[int] = (),
    mod_role_ids: Iterable[int] = (),
) -> Level:
    """Highest matching level wins. Pass guild_owner_id=None for DMs."""
    if user_id in set(owner_ids):
        return Level.BOTOWNER
    if guild_owner_id is None:
        return Level.USER
    if user_id == guild_owner_id:
        return Level.SERVEROWNER
    if manage_guild or manage_messages or set(role_ids) & set(mod_role_ids):
        return Level.SERVERMODERATOR
    return Level.USER


def level_for(
    user: discord.abc.User,
    guild: discord.Guild | None,
    owner_ids: Iterable[int],
    mod_role_ids: Iterable[int] = (),
) -> Level:
    if guild is None or not isinstance(user, discord.Member):
        return resolve_level(user.id, owner_ids)
    perms = user.guild_permissions
    return resolve_level(
        user.id,
        owner_ids,
        guild_owner_id=guild.owner_id,
        manage_guild=perms.manage_guild,
        manage_messages=perms.manage_messages,
        role_ids=[r.id for r in user.roles],
        mod_role_ids=mod_role_ids,
    )


def requires(minimum: Level):
    """Slash command check. Runs before the handler; the bot resolves the level."""

    async def predicate(interaction: discord.Interaction) -> bool:
        level = await interaction.client.level_for(interaction.user, interaction.guild)
        if level < minimum:
            raise app_commands.CheckFailure(f"This needs {minimum.label} or higher.")
        return True

    return app_commands.check(predicate)
