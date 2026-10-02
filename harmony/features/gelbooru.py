"""/gel: pull images from Gelbooru. NSFW results only ever go to age-restricted channels."""

from __future__ import annotations

import asyncio
import io
import logging
import os
from typing import Literal

import discord
import httpx
from discord import app_commands

log = logging.getLogger(__name__)

API_URL = "https://gelbooru.com/index.php"
MAX_IMAGES = 5
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp")
VIDEO_EXTS = (".mp4", ".webm")
TIMEOUT = httpx.Timeout(30.0)
DOWNLOAD_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; harmony-discord-bot)", "Referer": "https://gelbooru.com/"}
# Always excluded, whatever the user asks for.
BLOCKED_TAGS = ("loli", "shota", "toddlercon")


def channel_is_nsfw(channel: discord.abc.GuildChannel | discord.Thread | None) -> bool:
    """Threads inherit from their parent. DMs and unknown channels count as SFW."""
    if isinstance(channel, discord.Thread):
        channel = channel.parent
    return bool(channel is not None and getattr(channel, "is_nsfw", lambda: False)())


def nsfw_allowed(interaction: discord.Interaction) -> bool:
    """18+ server channels, and one-to-one DMs. Servers Harmony isn't in can't be checked, so they count as SFW."""
    channel = interaction.channel
    if interaction.guild_id is None:
        return bool(interaction.context.dm_channel) or (channel is not None and channel.type == discord.ChannelType.private)
    return channel_is_nsfw(channel)


def build_tags(tags: str, mode: str | None, nsfw_channel: bool) -> str:
    """The Gelbooru tag query. Rating is decided here, never by the user's own tags."""
    if not nsfw_channel:
        mode = "sfw"
    parts = [t for t in tags.split() if not t.lower().lstrip("-~").startswith("rating:")]
    if mode == "sfw":
        parts.append("rating:general")
    elif mode == "nsfw":
        parts.append("-rating:general")
    parts += [f"-{t}" for t in BLOCKED_TAGS]
    parts.append("sort:random")
    return " ".join(parts)


def pick_posts(posts: list[dict], count: int) -> list[dict]:
    """Posts whose file is an image or video Discord can play, up to `count`."""
    exts = IMAGE_EXTS + VIDEO_EXTS
    keep = [p for p in posts if file_ext(str(p.get("file_url", ""))) in exts]
    return keep[:count]


def _credentials() -> dict[str, str]:
    key, uid = os.environ.get("GELBOORU_API_KEY"), os.environ.get("GELBOORU_USER_ID")
    return {"api_key": key, "user_id": uid} if key and uid else {}


async def fetch_posts(client: httpx.AsyncClient, tags: str, limit: int) -> list[dict]:
    params = {"page": "dapi", "s": "post", "q": "index", "json": "1", "tags": tags, "limit": str(limit)}
    r = await client.get(API_URL, params=params | _credentials())
    r.raise_for_status()
    data = r.json()
    posts = data.get("post", []) if isinstance(data, dict) else []
    return posts if isinstance(posts, list) else []


def file_ext(url: str) -> str:
    return os.path.splitext(url.lower().split("?")[0])[1]


async def download(client: httpx.AsyncClient, url: str, limit: int) -> bytes | None:
    """The file's bytes, or None if it can't be fetched or is over `limit`."""
    try:
        async with client.stream("GET", url, headers=DOWNLOAD_HEADERS, follow_redirects=True) as r:
            r.raise_for_status()
            buf = bytearray()
            async for chunk in r.aiter_bytes():
                buf += chunk
                if len(buf) > limit:
                    return None
            return bytes(buf)
    except httpx.HTTPError as e:
        log.warning("Gelbooru download failed for %s: %s", url, e)
        return None


async def fetch_media(client: httpx.AsyncClient, post: dict, limit: int) -> discord.File | None:
    """The post's media as an upload. Images fall back to the smaller sample if the original is too big."""
    urls = [post["file_url"]]
    if file_ext(post["file_url"]) in IMAGE_EXTS and post.get("sample_url"):
        urls.append(post["sample_url"])
    for url in urls:
        data = await download(client, url, limit)
        if data is not None:
            return discord.File(io.BytesIO(data), filename=f"{post.get('id')}{file_ext(url)}")
    return None


@app_commands.command(name="gel", description="Get images from Gelbooru")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(
    tags="Space-separated tags, e.g. 'splatoon_3 smile'",
    count=f"How many images (1-{MAX_IMAGES})",
    mode="nsfw/sfw. Defaults to both in DMs and 18+ channels, sfw only elsewhere",
)
async def gel(
    interaction: discord.Interaction,
    tags: app_commands.Range[str, 1, 200],
    count: app_commands.Range[int, 1, MAX_IMAGES] = 1,
    mode: Literal["sfw", "nsfw"] | None = None,
):
    nsfw_channel = nsfw_allowed(interaction)
    if mode == "nsfw" and not nsfw_channel:
        await interaction.response.send_message("NSFW images are only allowed in DMs and 18+ channels.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    query = build_tags(tags, mode, nsfw_channel)
    limit = interaction.filesize_limit
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            posts = pick_posts(await fetch_posts(client, query, count * 3), count)
            # Discord's upload limit covers the whole message, so share it between the files.
            files = await asyncio.gather(*(fetch_media(client, p, limit // len(posts)) for p in posts if posts))
    except (httpx.HTTPError, ValueError) as e:
        log.warning("Gelbooru request failed: %s", e)
        await interaction.followup.send("Gelbooru isn't answering right now.")
        return
    if not posts:
        await interaction.followup.send("No results for those tags.")
        return
    files = [f for f in files if f is not None]
    if not files:
        await interaction.followup.send("Couldn't download any of those.")
        return
    try:
        await interaction.followup.send(files=files)
    except discord.HTTPException as e:
        log.warning("Gelbooru upload failed: %s", e)
        await interaction.followup.send("Discord wouldn't take those files.")
