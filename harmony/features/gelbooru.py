"""/gel: pull images from Gelbooru. NSFW results only ever go to age-restricted channels."""

from __future__ import annotations

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


def pick_posts(posts: list[dict], count: int) -> list[dict]:
    """Posts whose file is an image or video Discord can play, up to `count`."""
    exts = IMAGE_EXTS + VIDEO_EXTS
    keep = [p for p in posts if str(p.get("file_url", "")).lower().split("?")[0].endswith(exts)]
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
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            posts = pick_posts(await fetch_posts(client, query, count * 3), count)
    except (httpx.HTTPError, ValueError) as e:
        log.warning("Gelbooru request failed: %s", e)
        await interaction.followup.send("Gelbooru isn't answering right now.")
        return
    if not posts:
        await interaction.followup.send("No results for those tags.")
        return
    await interaction.followup.send("\n".join(p["file_url"] for p in posts))
