from harmony.permissions import Level, resolve_level

OWNER, GUILD_OWNER, MOD, USER, MOD_ROLE = 1, 2, 3, 4, 99


def lvl(user_id, **kw):
    kw.setdefault("guild_owner_id", GUILD_OWNER)
    return resolve_level(user_id, [OWNER], **kw)


def test_botowner_by_id():
    assert lvl(OWNER) == Level.BOTOWNER


def test_botowner_beats_serverowner():
    assert resolve_level(OWNER, [OWNER], guild_owner_id=OWNER) == Level.BOTOWNER


def test_serverowner():
    assert lvl(GUILD_OWNER) == Level.SERVEROWNER


def test_mod_by_permission():
    assert lvl(MOD, manage_guild=True) == Level.SERVERMODERATOR
    assert lvl(MOD, manage_messages=True) == Level.SERVERMODERATOR


def test_mod_by_configured_role():
    assert lvl(MOD, role_ids=[5, MOD_ROLE], mod_role_ids=[MOD_ROLE]) == Level.SERVERMODERATOR
    assert lvl(MOD, role_ids=[5], mod_role_ids=[MOD_ROLE]) == Level.USER


def test_plain_user():
    assert lvl(USER) == Level.USER


def test_dm_only_botowner_or_user():
    assert resolve_level(OWNER, [OWNER]) == Level.BOTOWNER
    # Guild perms are meaningless in DMs.
    assert resolve_level(USER, [OWNER], manage_guild=True) == Level.USER


def test_levels_are_ordered():
    assert Level.USER < Level.SERVERMODERATOR < Level.SERVEROWNER < Level.BOTOWNER


class FakeUser:
    def __init__(self, id, name):
        self.id, self.name, self.display_name = id, name, name


def test_user_named_like_owner_is_user():
    from harmony.permissions import level_for

    impostor = FakeUser(USER, "Harmony's Creator")
    assert level_for(impostor, None, [OWNER]) == Level.USER


async def test_webhook_and_bot_messages_ignored():
    from types import SimpleNamespace
    from harmony.features.chat import ChatHandler

    calls = []

    class Store:
        async def is_blocked(self, _):
            calls.append("reached")
            return False

    handler = ChatHandler.__new__(ChatHandler)
    handler.bot = SimpleNamespace(store=Store(), user=SimpleNamespace(id=1000))
    webhook = SimpleNamespace(author=SimpleNamespace(bot=False, id=OWNER), webhook_id=123)
    bot_msg = SimpleNamespace(author=SimpleNamespace(bot=True, id=5), webhook_id=None)
    await handler.handle(webhook)
    await handler.handle(bot_msg)
    assert calls == []
