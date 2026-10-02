"""/gel: pull images from Gelbooru. NSFW results only ever go to age-restricted channels."""

from __future__ import annotations

import io
import logging
import os
from typing import Literal

import discord
import httpx
from discord import app_commands

log = logging.getLogger(__name__)

API_URL = "https://gelbooru.com/index.php"
MAX_IMAGES = 10  # Discord allows 10 embeds per message
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp")
VIDEO_EXTS = (".mp4", ".webm")
TIMEOUT = httpx.Timeout(30.0)
DOWNLOAD_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; harmony-discord-bot)", "Referer": "https://gelbooru.com/"}
DEFAULT_UPLOAD_LIMIT = 10 * 1024 * 1024
ARTIST_TAG_TYPE = 1
# Always excluded, whatever the user asks for.
BLOCKED_TAGS = ("loli", "shota", "toddlercon")


def channel_is_nsfw(channel: discord.abc.GuildChannel | discord.Thread | None) -> bool:
    """Threads inherit from their parent. DMs and unknown channels count as SFW."""
    if isinstance(channel, discord.Thread):
        channel = channel.parent
    return bool(channel is not None and getattr(channel, "is_nsfw", lambda: False)())


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


def file_ext(post: dict) -> str:
    url = str(post.get("file_url", "")).lower().split("?")[0]
    return os.path.splitext(url)[1]


def pick_posts(posts: list[dict], count: int) -> list[dict]:
    """Posts whose file is an image or video Discord can play, up to `count`."""
    keep = [p for p in posts if file_ext(p) in IMAGE_EXTS + VIDEO_EXTS]
    return keep[:count]


def attribution(post: dict, artists: list[str]) -> str:
    lines = []
    if artists:
        lines.append("Artist: " + ", ".join(a.replace("_", " ") for a in artists))
    if post.get("owner"):
        lines.append(f"Posted by: {post['owner']}")
    lines.append(f"Score: {post.get('score', 0)}")
    return "\n".join(lines)


def _credentials() -> dict[str, str]:
    key, uid = os.environ.get("GELBOORU_API_KEY"), os.environ.get("GELBOORU_USER_ID")
    return {"api_key": key, "user_id": uid} if key and uid else {}


async def _api(client: httpx.AsyncClient, **params: str) -> dict:
    r = await client.get(API_URL, params={"page": "dapi", "q": "index", "json": "1", **params, **_credentials()})
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, dict) else {}


async def fetch_posts(client: httpx.AsyncClient, tags: str, limit: int) -> list[dict]:
    posts = (await _api(client, s="post", tags=tags, limit=str(limit))).get("post", [])
    return posts if isinstance(posts, list) else []


async def fetch_artists(client: httpx.AsyncClient, post: dict) -> list[str]:
    """Artist tags of a post. Best effort: any failure just means no artist line."""
    names = str(post.get("tags", "")).strip()
    if not names:
        return []
    try:
        found = (await _api(client, s="tag", names=names)).get("tag", [])
    except (httpx.HTTPError, ValueError):
        return []
    if isinstance(found, dict):
        found = [found]
    return [t["name"] for t in found if isinstance(t, dict) and int(t.get("type", 0)) == ARTIST_TAG_TYPE]


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


async def build_message(client: httpx.AsyncClient, post: dict, limit: int) -> dict:
    """followup.send kwargs for one post: the media as an upload, attribution under it."""
    artists = await fetch_artists(client, post)
    embed = discord.Embed(
        title=f"Post {post.get('id')}",
        url=f"https://gelbooru.com/index.php?page=post&s=view&id={post.get('id')}",
        description=attribution(post, artists),
    )
    ext = file_ext(post)
    data = await download(client, post["file_url"], limit)
    if data is None and ext in IMAGE_EXTS and post.get("sample_url"):
        data = await download(client, post["sample_url"], limit)
        ext = file_ext({"file_url": post["sample_url"]}) or ext
    if data is None:
        embed.description += f"\n[Open media]({post['file_url']})"
        if ext in IMAGE_EXTS:
            embed.set_image(url=post["file_url"])
        return {"embed": embed}
    name = f"{post.get('id')}{ext}"
    if ext in IMAGE_EXTS:
        embed.set_image(url=f"attachment://{name}")
    return {"embed": embed, "file": discord.File(io.BytesIO(data), filename=name)}


@app_commands.command(name="gel", description="Get images from Gelbooru")
@app_commands.describe(
    tags="Space-separated tags, e.g. 'splatoon_3 smile'",
    count=f"How many images (1-{MAX_IMAGES})",
    mode="nsfw/sfw. Defaults to both in 18+ channels, sfw only elsewhere",
)
async def gel(
    interaction: discord.Interaction,
    tags: app_commands.Range[str, 1, 200],
    count: app_commands.Range[int, 1, MAX_IMAGES] = 1,
    mode: Literal["sfw", "nsfw"] | None = None,
):
    nsfw_channel = channel_is_nsfw(interaction.channel)
    if mode == "nsfw" and not nsfw_channel:
        await interaction.response.send_message("NSFW images are only allowed in 18+ channels.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    query = build_tags(tags, mode, nsfw_channel)
    limit = interaction.guild.filesize_limit if interaction.guild else DEFAULT_UPLOAD_LIMIT
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": "harmony-discord-bot"}) as client:
            posts = pick_posts(await fetch_posts(client, query, count * 3), count)
            messages = [await build_message(client, p, limit) for p in posts]
    except (httpx.HTTPError, ValueError) as e:
        log.warning("Gelbooru request failed: %s", e)
        await interaction.followup.send("Gelbooru isn't answering right now.")
        return
    if not messages:
        await interaction.followup.send("No results for those tags.")
        return
    for m in messages:
        await interaction.followup.send(**m)
