async def test_facts_cap_drops_oldest(store):
    await store.add_facts(1, [f"f{i}" for i in range(5)], None, cap=3)
    assert await store.get_facts(1) == ["f2", "f3", "f4"]


async def test_facts_are_global_and_forgettable(store):
    await store.add_facts(1, ["Likes squids"], None, cap=30)
    assert await store.facts_for_users([1, 2]) == {1: ["Likes squids"]}
    assert await store.forget_facts(1) == 1
    assert await store.get_facts(1) == []


async def test_guild_seeding_only_once(store):
    assert await store.ensure_guild(7, [42], ["Vent"]) is True
    assert await store.quiet_words(7) == ["vent"]
    await store.set_quiet_word(7, "vent", False)
    assert await store.ensure_guild(7, [42], ["vent"]) is False
    assert await store.quiet_words(7) == []
    assert (await store.get_guild(7)).mod_role_ids == [42]


async def test_keywords_seeded_once(store):
    await store.seed_keywords_once(["gear", "Sea"])
    assert await store.keywords() == ["gear", "sea"]
    await store.set_keyword("gear", False)
    await store.seed_keywords_once(["gear"])
    assert await store.keywords() == ["sea"]


async def test_channel_flags_and_reset(store):
    await store.ensure_guild(7, [], [])
    await store.set_channel_enabled(7, 100, False)
    s = await store.get_guild(7)
    assert not s.channel_enabled(100) and s.channel_enabled(101)
    await store.add_message(100, 7, 5, "user", "hi")
    await store.set_summary(100, "notes", 1)
    await store.reset_channel(100)
    assert await store.recent_messages(100, 10) == []
    assert await store.get_summary(100) == ("", 0)


async def test_recent_messages_window(store):
    for i in range(5):
        await store.add_message(100, None, 5, "user", f"m{i}")
    assert [m.content for m in await store.recent_messages(100, 2)] == ["m3", "m4"]


async def test_db_persists(tmp_path):
    from harmony.memory.db import connect
    from harmony.memory.store import Store

    path = str(tmp_path / "p.db")
    db = await connect(path)
    await Store(db).block(9)
    await db.close()
    db = await connect(path)
    assert await Store(db).is_blocked(9)
    await db.close()


async def test_dm_log_retry_queue(store):
    dm_id = await store.log_dm(5, "in", "hello", ["http://a"], forwarded=False)
    pending = await store.unforwarded_dms()
    assert pending[0]["attachments"] == ["http://a"]
    await store.set_dm_forwarded(dm_id, True)
    assert await store.unforwarded_dms() == []
