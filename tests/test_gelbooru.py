from harmony.features.gelbooru import build_tags, pick_images


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


def test_pick_images_skips_video_and_limits():
    posts = [{"file_url": "a.mp4"}, {"file_url": "b.PNG"}, {"file_url": "c.jpg"}, {"file_url": "d.gif"}]
    assert pick_images(posts, 2) == [{"file_url": "b.PNG"}, {"file_url": "c.jpg"}]
