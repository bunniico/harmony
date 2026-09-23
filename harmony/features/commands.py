"""Slash commands under /harmony. Every command is gated by a code-side level check."""

from __future__ import annotations

import logging
import time
from typing import Literal

import discord
from discord import app_commands

from harmony.permissions import Level, requires

log = logging.getLogger(__name__)
MAX_DIRECTIVE_CHARS = 1500


def guild_required():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            raise app_commands.CheckFailure("Use this in a server.")
        return True

    return app_commands.check(predicate)


async def _say(interaction: discord.Interaction, text: str) -> None:
    await interaction.response.send_message(text[:2000], ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


harmony = app_commands.Group(name="harmony", description="Harmony bot controls")
memory = app_commands.Group(name="memory", description="What Harmony remembers about people", parent=harmony)
channel = app_commands.Group(name="channel", description="Per-channel settings", parent=harmony)
modrole = app_commands.Group(name="modrole", description="Roles that count as moderators", parent=harmony)
guild = app_commands.Group(name="guild", description="Server-wide settings", parent=harmony)
persona = app_commands.Group(name="persona", description="Persona files", parent=harmony)
directive = app_commands.Group(name="directive", description="Owner-only extra instruction", parent=harmony)
keywords = app_commands.Group(name="keywords", description="Words that can make Harmony chime in", parent=harmony)
quiet = app_commands.Group(name="quiet", description="Channels where Harmony stays silent", parent=harmony)
dm = app_commands.Group(name="dm", description="DM tools", parent=harmony)


# --- memory ---------------------------------------------------------------

async def _memory_target(interaction: discord.Interaction, user: discord.User | None) -> discord.abc.User | None:
    """Anyone may act on themselves; acting on others needs servermoderator or higher."""
    target = user or interaction.user
    if target.id != interaction.user.id:
        level = await interaction.client.level_for(interaction.user, interaction.guild)
        if level < Level.SERVERMODERATOR:
            await _say(interaction, "You can only do that for yourself.")
            return None
    return target


@memory.command(name="view", description="Show stored facts")
async def memory_view(interaction: discord.Interaction, user: discord.User | None = None):
    target = await _memory_target(interaction, user)
    if target is None:
        return
    facts = await interaction.client.store.get_facts(target.id)
    body = "\n".join(f"- {f}" for f in facts) if facts else "(nothing stored)"
    await _say(interaction, f"Facts for {target} ({target.id}):\n{body}")


@memory.command(name="forget", description="Delete stored facts")
async def memory_forget(interaction: discord.Interaction, user: discord.User | None = None):
    target = await _memory_target(interaction, user)
    if target is None:
        return
    n = await interaction.client.store.forget_facts(target.id)
    await _say(interaction, f"Forgot {n} fact(s) about {target}.")


# --- channel --------------------------------------------------------------

@channel.command(name="reset", description="Wipe this channel's history and summary")
@requires(Level.SERVERMODERATOR)
async def channel_reset(interaction: discord.Interaction):
    await interaction.client.store.reset_channel(interaction.channel_id)
    await _say(interaction, "Channel memory wiped.")


@channel.command(name="enable", description="Allow Harmony in this channel")
@guild_required()
@requires(Level.SERVERMODERATOR)
async def channel_enable(interaction: discord.Interaction):
    await interaction.client.store.set_channel_enabled(interaction.guild_id, interaction.channel_id, True)
    await _say(interaction, "Harmony is enabled in this channel.")


@channel.command(name="disable", description="Keep Harmony out of this channel")
@guild_required()
@requires(Level.SERVERMODERATOR)
async def channel_disable(interaction: discord.Interaction):
    await interaction.client.store.set_channel_enabled(interaction.guild_id, interaction.channel_id, False)
    await _say(interaction, "Harmony is disabled in this channel.")


# --- modrole / guild ------------------------------------------------------

@modrole.command(name="add", description="Treat a role as moderator")
@guild_required()
@requires(Level.SERVEROWNER)
async def modrole_add(interaction: discord.Interaction, role: discord.Role):
    await interaction.client.store.set_mod_role(interaction.guild_id, role.id, True)
    await _say(interaction, f"{role.name} now counts as moderator.")


@modrole.command(name="remove", description="Stop treating a role as moderator")
@guild_required()
@requires(Level.SERVEROWNER)
async def modrole_remove(interaction: discord.Interaction, role: discord.Role):
    await interaction.client.store.set_mod_role(interaction.guild_id, role.id, False)
    await _say(interaction, f"{role.name} no longer counts as moderator.")


@guild.command(name="disable", description="Turn Harmony off in this server")
@guild_required()
@requires(Level.SERVEROWNER)
async def guild_disable(interaction: discord.Interaction):
    await interaction.client.store.set_guild_enabled(interaction.guild_id, False)
    await _say(interaction, "Harmony is off in this server.")


@guild.command(name="enable", description="Turn Harmony back on in this server")
@guild_required()
@requires(Level.SERVEROWNER)
async def guild_enable(interaction: discord.Interaction):
    await interaction.client.store.set_guild_enabled(interaction.guild_id, True)
    await _say(interaction, "Harmony is on in this server.")


# --- botowner -------------------------------------------------------------

@persona.command(name="reload", description="Reload persona files from disk")
@requires(Level.BOTOWNER)
async def persona_reload(interaction: discord.Interaction):
    try:
        interaction.client.reload_persona()
    except OSError as e:
        await _say(interaction, f"Reload failed, keeping the old persona: {e}")
        return
    await _say(interaction, "Persona reloaded.")


@directive.command(name="set", description="Set the owner directive")
@requires(Level.BOTOWNER)
async def directive_set(interaction: discord.Interaction, text: app_commands.Range[str, 1, MAX_DIRECTIVE_CHARS]):
    await interaction.client.store.set_directive(text)
    log.info("Directive set by %s", interaction.user.id)
    await _say(interaction, "Directive set.")


@directive.command(name="clear", description="Clear the owner directive")
@requires(Level.BOTOWNER)
async def directive_clear(interaction: discord.Interaction):
    await interaction.client.store.set_directive("")
    await _say(interaction, "Directive cleared.")


@harmony.command(name="block", description="Add a user to the global blocklist")
@requires(Level.BOTOWNER)
async def block(interaction: discord.Interaction, user: discord.User, reason: str = ""):
    if user.id in interaction.client.cfg.owner_ids:
        await _say(interaction, "Bot owners can't be blocked.")
        return
    await interaction.client.store.block(user.id, reason)
    await _say(interaction, f"Blocked {user} ({user.id}).")


@harmony.command(name="unblock", description="Remove a user from the global blocklist")
@requires(Level.BOTOWNER)
async def unblock(interaction: discord.Interaction, user: discord.User):
    await interaction.client.store.unblock(user.id)
    await _say(interaction, f"Unblocked {user} ({user.id}).")


@keywords.command(name="list", description="Show the keyword list")
@requires(Level.BOTOWNER)
async def keywords_list(interaction: discord.Interaction):
    await _say(interaction, "Keywords: " + (", ".join(interaction.client.keywords) or "(none)"))


@keywords.command(name="add", description="Add a keyword")
@requires(Level.BOTOWNER)
async def keywords_add(interaction: discord.Interaction, word: app_commands.Range[str, 1, 50]):
    await interaction.client.set_keyword(word, True)
    await _say(interaction, f"Added keyword: {word.lower()}")


@keywords.command(name="remove", description="Remove a keyword")
@requires(Level.BOTOWNER)
async def keywords_remove(interaction: discord.Interaction, word: str):
    await interaction.client.set_keyword(word, False)
    await _say(interaction, f"Removed keyword: {word.lower()}")


# --- quiet channels -------------------------------------------------------

@quiet.command(name="list", description="Show this server's quiet words and quiet channels")
@guild_required()
@requires(Level.SERVERMODERATOR)
async def quiet_list(interaction: discord.Interaction):
    store = interaction.client.store
    words = await store.quiet_words(interaction.guild_id)
    chans = await store.quiet_channels(interaction.guild_id)
    await _say(
        interaction,
        "Quiet words: " + (", ".join(words) or "(none)") + "\nQuiet channels: " + (" ".join(f"<#{c}>" for c in chans) or "(none)"),
    )


@quiet.command(name="word", description="Add or remove a quiet channel-name word")
@guild_required()
@requires(Level.SERVERMODERATOR)
async def quiet_word(interaction: discord.Interaction, action: Literal["add", "remove"], word: app_commands.Range[str, 1, 50]):
    await interaction.client.store.set_quiet_word(interaction.guild_id, word, action == "add")
    await _say(interaction, f"Quiet word {'added' if action == 'add' else 'removed'}: {word.lower()}")


@quiet.command(name="channel", description="Mark or unmark a specific channel as quiet")
@guild_required()
@requires(Level.SERVERMODERATOR)
async def quiet_channel(
    interaction: discord.Interaction,
    action: Literal["add", "remove"],
    target: discord.TextChannel | discord.ForumChannel | discord.Thread,
):
    await interaction.client.store.set_quiet_channel(interaction.guild_id, target.id, action == "add")
    await _say(interaction, f"<#{target.id}> is {'now' if action == 'add' else 'no longer'} a quiet channel.")


# --- DM reply / status ----------------------------------------------------

@dm.command(name="reply", description="Reply to a forwarded DM through the bot")
@requires(Level.BOTOWNER)
async def dm_reply(interaction: discord.Interaction, user: discord.User, text: app_commands.Range[str, 1, 2000]):
    try:
        await interaction.client.dm.owner_reply(user, text)
    except discord.HTTPException as e:
        await _say(interaction, f"Couldn't DM {user}: {e}")
        return
    await _say(interaction, f"Sent to {user}.")


@harmony.command(name="status", description="Token usage, cache hit rate, uptime")
@requires(Level.BOTOWNER)
async def status(interaction: discord.Interaction):
    bot = interaction.client
    u = bot.ai.usage
    uptime = int(time.monotonic() - bot.started)
    hashes = "\n".join(f"- `{name}`: `{h[:16]}`" for name, h in bot.file_hashes().items())
    await _say(
        interaction,
        f"Uptime: {uptime // 3600}h {uptime % 3600 // 60}m\n"
        f"Calls: {u.calls}  |  input: {u.input_tokens}  output: {u.output_tokens}\n"
        f"Cache read: {u.cache_read}  write: {u.cache_write}  hit rate: {u.cache_hit_rate:.1%}\n"
        f"File hashes (SHA-256):\n{hashes}",
    )


def register(tree: app_commands.CommandTree) -> None:
    tree.add_command(harmony)

    @tree.error
    async def on_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            msg = str(error) or "You can't use that."
        else:
            log.exception("Command error", exc_info=error)
            msg = "Something went wrong."
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
