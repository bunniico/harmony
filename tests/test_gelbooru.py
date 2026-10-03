from types import SimpleNamespace

import discord

from harmony.features.gelbooru import build_tags, can_delete, nsfw_allowed, pick_posts


def test_sfw_channel_forces_sfw_even_if_nsfw_asked():
    assert "rating:general" in build_tags("cat", "nsfw", nsfw_channel=False).split()
    assert "rating:general" in build_tags("cat", None, nsfw_channel=False).split()


def test_nsfw_channel_modes():
    assert "-rating:general" in build_tags("cat", "nsfw", True).split()
    both = build_tags("cat", None, True).split()
    assert "rating:general" not in both and "-rating:general" not in both
    assert "rating:general" in build_tags("cat", "sfw", True).split()


def test_user_rating_tags_stripped():
    q = build_tags("cat rating:explicit -rating:general ~rating:safe", None, False).split()
    assert q.count("rating:general") == 1
    assert "rating:explicit" not in q and "-rating:general" not in q


def test_blocked_tags_always_excluded():
    q = build_tags("cat", "nsfw", True).split()
    assert {"-loli", "-shota", "-toddlercon"} <= set(q)


def test_pick_posts_keeps_images_and_video_and_limits():
    posts = [{"file_url": "a.zip"}, {"file_url": "b.PNG"}, {"file_url": "c.mp4?x=1"}, {"file_url": "d.gif"}]
    assert pick_posts(posts, 2) == [{"file_url": "b.PNG"}, {"file_url": "c.mp4?x=1"}]


def _ix(guild_id, channel_type, bot_dm=False):
    return SimpleNamespace(
        guild_id=guild_id,
        channel=SimpleNamespace(type=channel_type),
        context=SimpleNamespace(dm_channel=bot_dm),
    )


def test_nsfw_allowed_in_dms_only_one_to_one():
    assert nsfw_allowed(_ix(None, discord.ChannelType.private))
    assert nsfw_allowed(_ix(None, discord.ChannelType.private, bot_dm=True))
    assert not nsfw_allowed(_ix(None, discord.ChannelType.group))


def test_nsfw_not_allowed_in_unchecked_server_channel():
    # A server Harmony isn't in: a partial channel with no is_nsfw.
    assert not nsfw_allowed(_ix(123, discord.ChannelType.text))


def test_can_delete_requester_or_manage_messages():
    assert can_delete(1, 1, discord.Permissions.none())
    assert can_delete(2, 1, discord.Permissions(manage_messages=True))
    assert not can_delete(2, 1, discord.Permissions.none())
