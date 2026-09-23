"""Read/write helpers over the SQLite database."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

import aiosqlite


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class StoredMessage:
    id: int
    author_id: int
    role: str
    content: str
    flagged: bool
    source: str


@dataclass
class GuildSettings:
    enabled: bool = True
    mod_role_ids: list[int] = field(default_factory=list)
    channel_flags: dict[int, bool] = field(default_factory=dict)  # channel_id -> enabled

    def channel_enabled(self, channel_id: int) -> bool:
        return self.channel_flags.get(channel_id, True)


def _msg(row) -> StoredMessage:
    return StoredMessage(row["id"], row["author_id"], row["role"], row["content"], bool(row["flagged"]), row["source"])


class Store:
    def __init__(self, db: aiosqlite.Connection):
        self.db = db

    async def _one(self, sql: str, args=()):
        return await (await self.db.execute(sql, args)).fetchone()

    async def _all(self, sql: str, args=()):
        return await (await self.db.execute(sql, args)).fetchall()

    async def _write(self, sql: str, args=()) -> aiosqlite.Cursor:
        cur = await self.db.execute(sql, args)
        await self.db.commit()
        return cur

    # --- messages ---------------------------------------------------------

    async def add_message(
        self, channel_id: int, guild_id: int | None, author_id: int, role: str,
        content: str, flagged: bool = False, source: str = "chat",
    ) -> int:
        cur = await self._write(
            "INSERT INTO messages (channel_id, guild_id, author_id, role, content, flagged, source, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (channel_id, guild_id, author_id, role, content, int(flagged), source, _now()),
        )
        return cur.lastrowid

    async def recent_messages(self, channel_id: int, limit: int) -> list[StoredMessage]:
        rows = await self._all(
            "SELECT * FROM messages WHERE channel_id = ? ORDER BY id DESC LIMIT ?", (channel_id, limit)
        )
        return [_msg(r) for r in reversed(rows)]

    async def messages_after(self, channel_id: int, after_id: int) -> list[StoredMessage]:
        rows = await self._all(
            "SELECT * FROM messages WHERE channel_id = ? AND id > ? ORDER BY id", (channel_id, after_id)
        )
        return [_msg(r) for r in rows]

    async def get_summary(self, channel_id: int) -> tuple[str, int]:
        row = await self._one("SELECT summary, last_message_id FROM channel_summaries WHERE channel_id = ?", (channel_id,))
        return (row["summary"], row["last_message_id"]) if row else ("", 0)

    async def set_summary(self, channel_id: int, summary: str, last_message_id: int) -> None:
        await self._write(
            "INSERT INTO channel_summaries (channel_id, summary, last_message_id, updated_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(channel_id) DO UPDATE SET summary = excluded.summary,"
            " last_message_id = excluded.last_message_id, updated_at = excluded.updated_at",
            (channel_id, summary, last_message_id, _now()),
        )

    async def reset_channel(self, channel_id: int) -> None:
        await self.db.execute("DELETE FROM messages WHERE channel_id = ?", (channel_id,))
        await self._write("DELETE FROM channel_summaries WHERE channel_id = ?", (channel_id,))

    # --- user facts (global across servers) -------------------------------

    async def add_facts(self, user_id: int, facts: list[str], source_message_id: int | None, cap: int) -> None:
        for fact in facts:
            await self.db.execute(
                "INSERT INTO user_facts (user_id, fact, source_message_id, created_at) VALUES (?, ?, ?, ?)",
                (user_id, fact, source_message_id, _now()),
            )
        # Oldest dropped first once over the cap.
        await self._write(
            "DELETE FROM user_facts WHERE user_id = ? AND id NOT IN"
            " (SELECT id FROM user_facts WHERE user_id = ? ORDER BY id DESC LIMIT ?)",
            (user_id, user_id, cap),
        )

    async def get_facts(self, user_id: int) -> list[str]:
        rows = await self._all("SELECT fact FROM user_facts WHERE user_id = ? ORDER BY id", (user_id,))
        return [r["fact"] for r in rows]

    async def facts_for_users(self, user_ids: list[int]) -> dict[int, list[str]]:
        return {uid: facts for uid in user_ids if (facts := await self.get_facts(uid))}

    async def forget_facts(self, user_id: int) -> int:
        cur = await self._write("DELETE FROM user_facts WHERE user_id = ?", (user_id,))
        return cur.rowcount

    # --- guild settings ---------------------------------------------------

    async def ensure_guild(self, guild_id: int, mod_role_ids: list[int], quiet_words: list[str]) -> bool:
        """First time a guild is seen, seed its settings and quiet words. Returns True if seeded."""
        if await self._one("SELECT 1 FROM guild_settings WHERE guild_id = ?", (guild_id,)):
            return False
        await self.db.execute(
            "INSERT INTO guild_settings (guild_id, enabled, mod_role_ids, enabled_channels) VALUES (?, 1, ?, '{}')",
            (guild_id, json.dumps(mod_role_ids)),
        )
        await self.db.executemany(
            "INSERT OR IGNORE INTO quiet_words (guild_id, word) VALUES (?, ?)",
            [(guild_id, w.lower()) for w in quiet_words],
        )
        await self.db.commit()
        return True

    async def get_guild(self, guild_id: int) -> GuildSettings:
        row = await self._one("SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,))
        if not row:
            return GuildSettings()
        flags = json.loads(row["enabled_channels"] or "{}")
        return GuildSettings(
            enabled=bool(row["enabled"]),
            mod_role_ids=json.loads(row["mod_role_ids"] or "[]"),
            channel_flags={int(k): bool(v) for k, v in flags.items()},
        )

    async def _save_guild(self, guild_id: int, s: GuildSettings) -> None:
        await self._write(
            "INSERT INTO guild_settings (guild_id, enabled, mod_role_ids, enabled_channels) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(guild_id) DO UPDATE SET enabled = excluded.enabled,"
            " mod_role_ids = excluded.mod_role_ids, enabled_channels = excluded.enabled_channels",
            (guild_id, int(s.enabled), json.dumps(s.mod_role_ids),
             json.dumps({str(k): v for k, v in s.channel_flags.items()})),
        )

    async def set_guild_enabled(self, guild_id: int, enabled: bool) -> None:
        s = await self.get_guild(guild_id)
        s.enabled = enabled
        await self._save_guild(guild_id, s)

    async def set_channel_enabled(self, guild_id: int, channel_id: int, enabled: bool) -> None:
        s = await self.get_guild(guild_id)
        s.channel_flags[channel_id] = enabled
        await self._save_guild(guild_id, s)

    async def set_mod_role(self, guild_id: int, role_id: int, present: bool) -> None:
        s = await self.get_guild(guild_id)
        roles = set(s.mod_role_ids)
        roles.add(role_id) if present else roles.discard(role_id)
        s.mod_role_ids = sorted(roles)
        await self._save_guild(guild_id, s)

    # --- owner directive --------------------------------------------------

    async def get_directive(self) -> str:
        row = await self._one("SELECT text FROM owner_directive WHERE id = 1")
        return row["text"] if row and row["text"] else ""

    async def set_directive(self, text: str) -> None:
        await self._write(
            "INSERT INTO owner_directive (id, text, updated_at) VALUES (1, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET text = excluded.text, updated_at = excluded.updated_at",
            (text, _now()),
        )

    # --- blocklist --------------------------------------------------------

    async def is_blocked(self, user_id: int) -> bool:
        return bool(await self._one("SELECT 1 FROM blocklist WHERE user_id = ?", (user_id,)))

    async def block(self, user_id: int, reason: str = "") -> None:
        await self._write(
            "INSERT OR REPLACE INTO blocklist (user_id, reason, created_at) VALUES (?, ?, ?)", (user_id, reason, _now())
        )

    async def unblock(self, user_id: int) -> None:
        await self._write("DELETE FROM blocklist WHERE user_id = ?", (user_id,))

    # --- DM log -----------------------------------------------------------

    async def log_dm(self, user_id: int, direction: str, content: str, attachments: list[str], forwarded: bool) -> int:
        cur = await self._write(
            "INSERT INTO dm_log (user_id, direction, content, attachments, created_at, forwarded) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, direction, content, json.dumps(attachments), _now(), int(forwarded)),
        )
        return cur.lastrowid

    async def set_dm_forwarded(self, dm_id: int, forwarded: bool) -> None:
        await self._write("UPDATE dm_log SET forwarded = ? WHERE id = ?", (int(forwarded), dm_id))

    async def unforwarded_dms(self) -> list[dict]:
        rows = await self._all("SELECT * FROM dm_log WHERE direction = 'in' AND forwarded = 0 ORDER BY id")
        return [dict(r) | {"attachments": json.loads(r["attachments"] or "[]")} for r in rows]

    # --- keywords (global) ------------------------------------------------

    async def seed_keywords_once(self, words: list[str]) -> None:
        if await self._one("SELECT 1 FROM meta WHERE key = 'keywords_seeded'"):
            return
        await self.db.executemany("INSERT OR IGNORE INTO keywords (word) VALUES (?)", [(w.lower(),) for w in words])
        await self._write("INSERT INTO meta (key, value) VALUES ('keywords_seeded', '1')")

    async def keywords(self) -> list[str]:
        return [r["word"] for r in await self._all("SELECT word FROM keywords ORDER BY word")]

    async def set_keyword(self, word: str, present: bool) -> None:
        if present:
            await self._write("INSERT OR IGNORE INTO keywords (word) VALUES (?)", (word.lower(),))
        else:
            await self._write("DELETE FROM keywords WHERE word = ?", (word.lower(),))

    # --- quiet channels ---------------------------------------------------

    async def quiet_words(self, guild_id: int) -> list[str]:
        rows = await self._all("SELECT word FROM quiet_words WHERE guild_id = ? ORDER BY word", (guild_id,))
        return [r["word"] for r in rows]

    async def set_quiet_word(self, guild_id: int, word: str, present: bool) -> None:
        if present:
            await self._write("INSERT OR IGNORE INTO quiet_words (guild_id, word) VALUES (?, ?)", (guild_id, word.lower()))
        else:
            await self._write("DELETE FROM quiet_words WHERE guild_id = ? AND word = ?", (guild_id, word.lower()))

    async def quiet_channels(self, guild_id: int) -> list[int]:
        rows = await self._all("SELECT channel_id FROM quiet_channels WHERE guild_id = ?", (guild_id,))
        return [r["channel_id"] for r in rows]

    async def set_quiet_channel(self, guild_id: int, channel_id: int, present: bool) -> None:
        if present:
            await self._write(
                "INSERT OR IGNORE INTO quiet_channels (guild_id, channel_id) VALUES (?, ?)", (guild_id, channel_id)
            )
        else:
            await self._write(
                "DELETE FROM quiet_channels WHERE guild_id = ? AND channel_id = ?", (guild_id, channel_id)
            )
