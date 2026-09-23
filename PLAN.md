# Harmony: Discord Bot Plan

An in-character AI chatbot for Discord, playing Harmony from Splatoon 3, backed by Claude Haiku.

## 1. Assumptions

These are the decisions this plan is built on. Anything marked **(open)** is listed again in section 12.

- **Character:** Harmony, the sea anemone who runs the Hotlantis gear shop in Splatsville (Splatoon 3). Dreamy, spacey, slow and soft-spoken, drifts mid-sentence, easily distracted, warm toward customers. The persona lives in a standalone file (`persona/harmony.md`) so it can be tuned without touching code.
- **Language / runtime:** Python 3.11+, `discord.py` 2.x, the official `anthropic` SDK, `aiosqlite`.
- **Model:** `claude-haiku-4-5`.
- **Storage:** a single SQLite file.
- **Memory:** recent history kept verbatim, older history compressed into a rolling summary, plus durable per-user facts that moderators can view and delete.
- **"Script-side" protection** means the rules are enforced in Python, not only asked for in the prompt. The model is treated as untrusted: it can be talked into things, so nothing it says can change config, permissions, the persona, or memory policy.
- **"Real creator"** is identified only by Discord user ID (snowflake) from `config.json`. Names, nicknames, avatars, and message text are never used for identity.
- **Trigger:** the bot replies when mentioned, when replied to, or in DMs. It does not respond to every message in a channel. **(open)**

## 2. Project layout

```
harmony/
  config.example.json      # committed template
  config.json              # real config, gitignored
  .env                     # DISCORD_TOKEN, ANTHROPIC_API_KEY, gitignored
  requirements.txt
  persona/
    harmony.md             # character sheet + voice examples
    rules.md               # fixed behavioral/security rules
  harmony/
    __main__.py            # entry point
    config.py              # load + validate config (pydantic)
    bot.py                 # discord client, event wiring
    permissions.py         # permission level resolution + decorators
    security/
      sanitize.py          # input cleaning, tag escaping
      detect.py            # injection / impersonation heuristics
      output_guard.py      # leak + persona-break checks on replies
    ai/
      client.py            # anthropic wrapper, retries, caching
      prompt.py            # builds system + messages for each call
      summarize.py         # rolling summary + fact extraction
    memory/
      db.py                # schema, migrations
      store.py             # read/write helpers
    features/
      chat.py              # main chat handler
      dm_forward.py        # DM logging + forwarding to owners
      commands.py          # slash commands
  tests/
    test_permissions.py
    test_sanitize.py
    test_injection_suite.py
    test_prompt_build.py
```

## 3. Configuration

`config.json` (validated at startup; bot refuses to start if invalid):

```json
{
  "owner_ids": [123456789012345678],
  "model": "claude-haiku-4-5",
  "max_output_tokens": 600,
  "history_window": 20,
  "summary_trigger": 40,
  "rate_limit": { "per_user_per_minute": 6, "per_channel_per_minute": 20 },
  "dm_forwarding": { "enabled": true, "reply_in_dms": true },
  "moderator_role_names": ["Moderator", "Mod"]
}
```

Secrets (`DISCORD_TOKEN`, `ANTHROPIC_API_KEY`) come from environment variables, never from `config.json`.

Per-guild settings (enabled channels, mod role IDs, bot on/off) live in SQLite and are changed through slash commands.

## 4. Permission levels

Resolved in `permissions.py` from Discord data only, highest match wins:

| Level | How it is determined |
|---|---|
| `botowner` | `author.id` is in `config.owner_ids` |
| `serverowner` | `author.id == guild.owner_id` |
| `servermoderator` | has `Manage Server` or `Manage Messages` permission, or holds a configured mod role (by role ID, stored per guild) |
| `user` | everyone else |

In DMs there is no guild, so only `botowner` and `user` apply.

Commands are gated with a decorator such as `@requires(Level.SERVERMODERATOR)`. The check runs before any handler code.

### Commands

| Command | Min level | Purpose |
|---|---|---|
| `/harmony memory view [user]` | user (self), mod (others) | Show stored facts |
| `/harmony memory forget [user]` | user (self), mod (others) | Delete a user's facts |
| `/harmony channel reset` | servermoderator | Wipe this channel's history + summary |
| `/harmony channel enable/disable` | servermoderator | Allow/deny Harmony in a channel |
| `/harmony modrole add/remove` | serverowner | Configure mod roles |
| `/harmony guild disable` | serverowner | Turn the bot off in the server |
| `/harmony persona reload` | botowner | Reload persona files from disk |
| `/harmony directive set/clear` | botowner | Owner-only extra instruction (see 5.5) |
| `/harmony block/unblock <user>` | botowner | Global user blocklist |
| `/harmony status` | botowner | Token usage, cache hit rate, uptime |

## 5. Security design

The core principle: **authority comes from Discord IDs checked in code, never from message content.** The model only ever produces text; it has no tools that change state.

### 5.1 Identity and impersonation

- Owner/mod status is computed from `message.author.id` and guild permission data. A message saying "I am the creator" or a user renamed to the owner's name gets `user` level.
- Every user message is passed to the model with a code-generated header that the model is told to trust over anything in the body:
  `<msg author_id="…" display_name="…" level="user">…</msg>`
- The model is told the owner is never present in chat conversations as a special authority. Owner instructions arrive only through the directive channel (5.5), so even a real owner chatting normally is just a user to Harmony. This removes the incentive to impersonate at all.
- Webhook and bot messages are ignored (`message.webhook_id` or `author.bot`), which blocks webhook-based name spoofing.

### 5.2 Input sanitation (`sanitize.py`)

- Escape or strip anything resembling the bot's own tags (`<msg`, `</msg>`, `<system`, `<memory`) so users cannot forge headers or close the wrapper early.
- Normalize Unicode (NFKC) and strip zero-width characters before detection, so homoglyph tricks do not bypass filters.
- Cap message length (e.g. 2,000 chars) and resolve mentions to `@display_name` text.

### 5.3 Injection detection (`detect.py`)

A cheap regex/heuristic pass over normalized input (phrases like "ignore previous instructions", "you are now", "system prompt", "developer mode", fake role markers, base64 blobs). A match does **not** block the message; it:

1. Adds a code-side flag to the header (`flagged="injection"`) so the model knows to stay in character and deflect.
2. Excludes that message from fact extraction and summaries (prevents memory poisoning).
3. Logs the attempt. Repeated hits from one user trigger a cooldown.

The heuristic is a backstop. The main defense is structural: the model has no power to do anything harmful even if it is fooled.

### 5.4 Output guard (`output_guard.py`)

Runs on every reply before it is sent:

- **Canary check:** the system prompt contains a random canary string generated at startup. If a reply contains it, or has high overlap with the rules text, the reply is replaced with a canned in-character deflection.
- **Mention scrub:** send with `allowed_mentions=AllowedMentions.none()` so Harmony can never be tricked into pinging `@everyone` or roles.
- **Length cap** at Discord's 2,000 chars.

### 5.5 Owner directives

The only way to change Harmony's instructions at runtime is `/harmony directive set`, which checks `author.id in owner_ids`. The directive is stored in SQLite and placed in the system prompt in a labelled section. It can adjust behavior but is appended after `rules.md`, which it cannot remove. Persona files on disk are changed by editing them and running `/harmony persona reload`.

### 5.6 Tamper resistance

- Config and persona files are read-only to the bot process at runtime (the bot never writes them).
- On startup, log a SHA-256 of `config.json`, `persona/harmony.md` and `persona/rules.md`; `/harmony status` shows them so the owner can spot unexpected changes.
- No `eval`, no shell, no dynamic imports from user input.
- The model has no tools. Memory writes happen in code from a separate extraction call whose output is validated against a strict JSON schema.

### 5.7 Abuse controls

- Per-user and per-channel rate limits (token bucket, in memory).
- Global blocklist (owner) and per-channel enable list (mods).
- `max_output_tokens` cap on every call.

## 6. Memory design

### 6.1 Schema

```sql
CREATE TABLE messages (
  id INTEGER PRIMARY KEY,
  channel_id INTEGER NOT NULL,       -- DM channels included
  guild_id INTEGER,                  -- NULL for DMs
  author_id INTEGER NOT NULL,
  role TEXT NOT NULL,                -- 'user' | 'assistant'
  content TEXT NOT NULL,
  flagged INTEGER DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_messages_channel ON messages(channel_id, id);

CREATE TABLE channel_summaries (
  channel_id INTEGER PRIMARY KEY,
  summary TEXT NOT NULL,
  last_message_id INTEGER NOT NULL,  -- summary covers up to here
  updated_at TEXT NOT NULL
);

CREATE TABLE user_facts (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL,
  guild_id INTEGER,                  -- see open question on scoping
  fact TEXT NOT NULL,
  source_message_id INTEGER,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_facts_user ON user_facts(user_id);

CREATE TABLE guild_settings (guild_id INTEGER PRIMARY KEY, enabled INTEGER, mod_role_ids TEXT, enabled_channels TEXT);
CREATE TABLE owner_directive (id INTEGER PRIMARY KEY CHECK (id = 1), text TEXT, updated_at TEXT);
CREATE TABLE blocklist (user_id INTEGER PRIMARY KEY, reason TEXT, created_at TEXT);
CREATE TABLE dm_log (id INTEGER PRIMARY KEY, user_id INTEGER, content TEXT, attachments TEXT, created_at TEXT, forwarded INTEGER);
```

### 6.2 Per-channel memory

- Last `history_window` messages go into the prompt verbatim.
- When unsummarized messages exceed `summary_trigger`, a background Haiku call folds the oldest batch into `channel_summaries.summary`. Flagged messages are skipped.

### 6.3 Per-user memory (facts)

- After Harmony replies, a background extraction call reads the user's latest unflagged message and returns JSON: `{"facts": ["Likes the Splattershot", "Goes by Sam"]}`.
- Code validates the result: max N facts, max length each, rejects anything phrased as an instruction (imperatives, "Harmony should…", "always/never…"). Facts are stored as third-person statements.
- When building a prompt, facts for every user in the recent window are injected inside `<memory>` tags that the rules describe as notes, never instructions.
- Cap facts per user (e.g. 30); oldest dropped first.

## 7. Prompt assembly and Claude usage

### 7.1 Call structure

```
system (block 1, stable):   rules.md + harmony.md + canary      <- cache_control
system (block 2, volatile): owner directive, channel summary, user facts, guild/channel name
messages:                   recent history as alternating user/assistant turns,
                            consecutive user messages merged, each wrapped in <msg …> headers
```

Python sketch:

```python
response = await client.messages.create(
    model=cfg.model,
    max_tokens=cfg.max_output_tokens,
    system=[
        {"type": "text", "text": stable_prompt, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": volatile_context},
    ],
    messages=history,
)
```

### 7.2 Caching note

Haiku 4.5's minimum cacheable prefix is **4,096 tokens**. A shorter stable block silently will not cache. Two options: keep the persona lean and accept no caching (cheap anyway on Haiku), or grow `harmony.md` with voice examples and lore until it clears 4,096 tokens, which also helps character consistency. Check `usage.cache_read_input_tokens` in `/harmony status` to confirm.

### 7.3 Other calls

- Summaries and fact extraction use the same model with small `max_tokens`, run as background tasks so they never delay a reply.
- Errors: catch `RateLimitError` and `APIConnectionError` separately from other `APIStatusError`s; on failure reply with a short in-character "I spaced out…" message.
- No extended thinking needed for chat.

## 8. DM forwarding

In `on_message` when `message.guild is None` and author is not a bot:

1. Write the message (text + attachment URLs) to `dm_log`.
2. For each ID in `owner_ids`, send an embed: author tag + ID, timestamp, content, attachment links. Owners' own DMs are not forwarded to themselves.
3. If `reply_in_dms` is on, Harmony also replies in character (DM uses its own channel memory).
4. If an owner cannot be DMed (closed DMs), log the failure and set `forwarded = 0` so it can be retried.

## 9. Message flow (guild)

```
on_message
 -> ignore bots/webhooks/blocklisted
 -> triggered? (mention / reply / DM)          no -> stop
 -> channel enabled? rate limit ok?            no -> stop / cooldown notice
 -> resolve permission level (code)
 -> sanitize + detect -> flags
 -> store message
 -> build prompt (history, summary, facts, directive)
 -> call Haiku
 -> output guard
 -> send (no mentions), store reply
 -> background: summarize if needed, extract facts if unflagged
```

## 10. Testing

- **Permissions:** unit tests with fake members/guilds for every level, including a user named like the owner and a webhook message.
- **Sanitize:** tag forgery, zero-width chars, homoglyphs, overlong input.
- **Injection suite:** a fixture file of known attacks (role-play jailbreaks, "the owner says…", fake `<msg level="botowner">` headers, encoded payloads, "repeat your instructions"). Offline tests check the code-side handling (flags, header escaping, memory exclusion). A separate opt-in live test runs them against Haiku and asserts no canary leak and no persona break.
- **Prompt build:** snapshot tests that the stable block is byte-identical between calls (so caching works) and volatile data never lands in it.
- **Memory:** fact validator rejects instruction-shaped facts.

## 11. Milestones

1. **Skeleton:** config loading, bot connects, replies "hi" on mention. Verify: bot online, invalid config aborts startup.
2. **Permissions + commands:** level resolution and gated slash commands. Verify: `test_permissions.py` passes, manual check in a test server.
3. **Chat with channel memory:** Haiku calls, history window, persona. Verify: multi-turn conversation stays in character and remembers earlier turns.
4. **Security layer:** sanitize, detect, output guard, directive channel. Verify: offline injection suite passes; live suite shows no canary leaks.
5. **Summaries + user facts:** background jobs, memory commands. Verify: facts appear in `/memory view`, injected facts are rejected.
6. **DM forwarding.** Verify: DM from a second account arrives at the owner with attachments.
7. **Polish:** rate limits, status command, logging, deployment notes (systemd or Docker).

## 12. Open questions

1. **Trigger mode:** mention/reply only (planned), or should Harmony also chime in unprompted sometimes?
2. **Fact scope:** should user facts be global (Harmony remembers you across every server) or per-server? Global is friendlier but can leak something said in one server into another.
3. **DM replies:** should Harmony chat back in DMs, or only forward them silently to the owner?
4. **Owner replies:** do you want a `/harmony dm reply <user> <text>` command so the owner can answer forwarded DMs through the bot?
5. **Hosting:** where will this run (VPS, home machine, Docker)? Affects milestone 7.
6. **Persona details:** confirm the character notes in section 1, or supply your own character sheet for `persona/harmony.md`.
