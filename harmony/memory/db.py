"""SQLite schema and migrations."""

from __future__ import annotations

import aiosqlite

# Each entry migrates from version i to i+1. Append only; never edit a shipped migration.
MIGRATIONS = [
    """
    CREATE TABLE messages (
      id INTEGER PRIMARY KEY,
      channel_id INTEGER NOT NULL,
      guild_id INTEGER,
      author_id INTEGER NOT NULL,
      role TEXT NOT NULL,
      content TEXT NOT NULL,
      flagged INTEGER DEFAULT 0,
      source TEXT NOT NULL DEFAULT 'chat',
      created_at TEXT NOT NULL
    );
    CREATE INDEX idx_messages_channel ON messages(channel_id, id);

    CREATE TABLE channel_summaries (
      channel_id INTEGER PRIMARY KEY,
      summary TEXT NOT NULL,
      last_message_id INTEGER NOT NULL,
      updated_at TEXT NOT NULL
    );

    CREATE TABLE user_facts (
      id INTEGER PRIMARY KEY,
      user_id INTEGER NOT NULL,
      fact TEXT NOT NULL,
      source_message_id INTEGER,
      created_at TEXT NOT NULL
    );
    CREATE INDEX idx_facts_user ON user_facts(user_id);

    CREATE TABLE guild_settings (guild_id INTEGER PRIMARY KEY, enabled INTEGER, mod_role_ids TEXT, enabled_channels TEXT);
    CREATE TABLE owner_directive (id INTEGER PRIMARY KEY CHECK (id = 1), text TEXT, updated_at TEXT);
    CREATE TABLE blocklist (user_id INTEGER PRIMARY KEY, reason TEXT, created_at TEXT);
    CREATE TABLE dm_log (id INTEGER PRIMARY KEY, user_id INTEGER, direction TEXT, content TEXT, attachments TEXT, created_at TEXT, forwarded INTEGER);
    CREATE TABLE keywords (word TEXT PRIMARY KEY);
    CREATE TABLE quiet_words (guild_id INTEGER, word TEXT, PRIMARY KEY (guild_id, word));
    CREATE TABLE quiet_channels (guild_id INTEGER, channel_id INTEGER, PRIMARY KEY (guild_id, channel_id));
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
    """,
]


async def connect(path: str) -> aiosqlite.Connection:
    db = await aiosqlite.connect(path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    (version,) = await (await db.execute("PRAGMA user_version")).fetchone()
    for i in range(version, len(MIGRATIONS)):
        await db.executescript(MIGRATIONS[i])
        await db.execute(f"PRAGMA user_version = {i + 1}")
    await db.commit()
    return db
