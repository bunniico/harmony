# Harmony: Discord Bot Plan

An in-character AI chatbot for Discord, playing Harmony from Splatoon 3, backed by Claude Haiku.

## 1. Assumptions

These are the decisions this plan is built on. Remaining open questions are in section 13.

- **Character:** Harmony, the sea anemone who runs the Hotlantis gear shop in Splatsville (Splatoon 3). Dreamy, spacey, slow and soft-spoken, drifts mid-sentence, easily distracted, warm toward customers. The persona lives in a standalone file (`persona/harmony.md`) so it can be tuned without touching code.
- **Language / runtime:** Python 3.11+, `discord.py` 2.x, the official `anthropic` SDK, `aiosqlite`.
- **Model:** `claude-haiku-4-5`.
- **Storage:** a single SQLite file.
- **Memory:** recent history kept verbatim, older history compressed into a rolling summary, plus durable per-user facts that moderators can view and delete.
- **"Script-side" protection** means the rules are enforced in Python, not only asked for in the prompt. The model is treated as untrusted: it can be talked into things, so nothing it says can change config, permissions, the persona, or memory policy.
- **"Real creator"** is identified only by Discord user ID (snowflake) from `config.json`. Names, nicknames, avatars, and message text are never used for identity.
- **Triggers:** Harmony always replies when @mentioned, replied to, or DMed. She also sometimes joins in on her own (section 9).
- **Quiet channels:** channels whose names look like "vent" are excluded by default. In those channels Harmony does not reply at all, even when mentioned (section 9.3).
- **User facts are global:** Harmony remembers a user's facts across every server. This was chosen because it is simpler.
- **Hosting:** Docker (section 10).

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
      triggers.py          # decides when Harmony speaks unprompted
  Dockerfile
  docker-compose.yml
  tests/
    test_permissions.py
    test_sanitize.py
    test_injection_suite.py
    test_prompt_build.py
    test_triggers.py
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
  "dm_forwarding": { "enabled": true },
  "moderator_role_names": ["Moderator", "Mod"],
  "unprompted": {
    "name_mention": true,
    "random_chance": 0.005,
    "channel_cooldown_seconds": 600,
    "default_keywords": ["hotlantis", "gear", "splatsville", "anemone", "fashion", "shopping", "jellyfish", "sea"]
  },
  "default_quiet_channel_words": ["vent", "venting", "vents", "grief", "mental", "support", "serious"]
}
```

The two `default_` lists only seed the database on first run. After that, the live lists are edited with slash commands (section 4).

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
| `/harmony keywords list/add/remove` | botowner | Global keyword list for unprompted replies |
| `/harmony quiet list` | servermoderator | Show this server's quiet words and quiet channels |
| `/harmony quiet word add/remove <word>` | servermoderator | Edit this server's quiet channel-name words |
| `/harmony quiet channel add/remove <#channel>` | servermoderator | Mark or unmark a specific channel as quiet |
| `/harmony dm reply <user> <text>` | botowner | Reply to a forwarded DM through the bot |
| `/harmony status` | botowner | Token usage, cache hit rate, uptime |

## 5. Security design

The core principle: **authority comes from Discord IDs checked in code, never from message content.** The model only ever produces text; its one set of tools (the adderall link, section 14) is offered only to the configured owner, and anything that loses data waits for her Confirm button.

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
- The model has no tools for the bot, the server, or its own memory (the adderall link in section 14 is the only toolset). Memory writes happen in code from a separate extraction call whose output is validated against a strict JSON schema.

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
  user_id INTEGER NOT NULL,          -- global: shared across all servers
  fact TEXT NOT NULL,
  source_message_id INTEGER,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_facts_user ON user_facts(user_id);

CREATE TABLE guild_settings (guild_id INTEGER PRIMARY KEY, enabled INTEGER, mod_role_ids TEXT, enabled_channels TEXT);
CREATE TABLE owner_directive (id INTEGER PRIMARY KEY CHECK (id = 1), text TEXT, updated_at TEXT);
CREATE TABLE blocklist (user_id INTEGER PRIMARY KEY, reason TEXT, created_at TEXT);
CREATE TABLE dm_log (id INTEGER PRIMARY KEY, user_id INTEGER, direction TEXT, content TEXT, attachments TEXT, created_at TEXT, forwarded INTEGER);  -- direction: 'in' | 'owner_reply'
CREATE TABLE keywords (word TEXT PRIMARY KEY);                                    -- global, botowner-managed
CREATE TABLE quiet_words (guild_id INTEGER, word TEXT, PRIMARY KEY (guild_id, word));
CREATE TABLE quiet_channels (guild_id INTEGER, channel_id INTEGER, PRIMARY KEY (guild_id, channel_id));
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
3. Harmony replies in character, the same as in a server. The DM uses its own channel memory.
4. If an owner cannot be DMed (closed DMs), log the failure and set `forwarded = 0` so it can be retried.

### Owner replies

`/harmony dm reply <user> <text>` (botowner only) sends `text` to that user's DMs from the bot account. It is:

- logged in `dm_log` with `direction = 'owner_reply'`;
- added to that DM's history as an assistant turn marked `source="owner"`. Harmony sees it as something said from her account, so the conversation stays coherent if the user answers it.

The text is sent exactly as the owner wrote it. It does not go through the model.

## 9. When Harmony speaks

### 9.1 Always

- @mentioned, replied to, or DMed.

### 9.2 Sometimes (unprompted), checked in this order

1. **Name in passing:** the word "Harmony" appears in the message (whole word, case-insensitive, after Unicode normalization).
2. **Keyword:** the message contains a word from the global `keywords` table (whole-word match).
3. **Random:** otherwise a `random_chance` roll (0.5% by default).

Unprompted replies have guardrails:

- A per-channel cooldown (`channel_cooldown_seconds`) so she can't be baited into spamming by repeating keywords.
- They never fire on messages flagged for injection.
- They count against the same rate limits as normal replies.

### 9.3 Quiet channels

A channel is quiet if either:

- its ID is in `quiet_channels` for that guild, or
- one of the **tokens** in its name matches a word in `quiet_words`. Channel names are split on `-`, `_`, spaces and emoji, so `#vent-space` matches `vent`, while `#events` does **not** (substring matching would wrongly catch it).

In a quiet channel Harmony stays silent, even when mentioned, and nothing is stored in memory. When a guild is first seen, `quiet_words` is seeded from `default_quiet_channel_words`. After that, servermoderator and above edit it with `/harmony quiet`. Botowner can do so in any server, because the permission levels are ordered.

### 9.4 Message flow (guild)

```
on_message
 -> ignore bots/webhooks/blocklisted
 -> quiet channel?                             yes -> stop (no reply, no storage)
 -> sanitize + detect -> flags
 -> triggered? mention/reply -> yes
               else unflagged + name/keyword/random + cooldown ok -> yes
                                               no -> stop
 -> channel enabled? rate limit ok?            no -> stop / cooldown notice
 -> resolve permission level (code)
 -> store message
 -> build prompt (history, summary, facts, directive)
 -> call Haiku
 -> output guard
 -> send (no mentions), store reply
 -> background: summarize if needed, extract facts if unflagged
```

## 10. Deployment (Docker)

- `Dockerfile`: `python:3.12-slim` base, install `requirements.txt`, run as a non-root user, `CMD ["python", "-m", "harmony"]`.
- `docker-compose.yml`: one `harmony` service with `restart: unless-stopped` and:
  - `env_file: .env` for `DISCORD_TOKEN` and `ANTHROPIC_API_KEY`;
  - `./config.json` and `./persona/` mounted **read-only**, which enforces the tamper rule in 5.6 at the container level;
  - a named volume at `/data` for `harmony.db`, so memory survives rebuilds.
- Logs go to stdout (`docker compose logs -f harmony`).
- Updating: `git pull && docker compose up -d --build`. Persona edits only need `/harmony persona reload`, no rebuild.

## 11. Testing

- **Permissions:** unit tests with fake members/guilds for every level, including a user named like the owner and a webhook message.
- **Sanitize:** tag forgery, zero-width chars, homoglyphs, overlong input.
- **Injection suite:** a fixture file of known attacks (role-play jailbreaks, "the owner says…", fake `<msg level="botowner">` headers, encoded payloads, "repeat your instructions"). Offline tests check the code-side handling (flags, header escaping, memory exclusion). A separate opt-in live test runs them against Haiku and asserts no canary leak and no persona break.
- **Prompt build:** snapshot tests that the stable block is byte-identical between calls (so caching works) and volatile data never lands in it.
- **Memory:** fact validator rejects instruction-shaped facts.
- **Triggers:** name and keyword whole-word matching (`"harmonyyy"` and `"gearbox"` do not match), cooldown blocks a second unprompted reply, flagged messages never trigger unprompted replies, random roll is injectable for tests.
- **Quiet channels:** `#vent`, `#vent-space`, `#late-night-venting` are quiet; `#events`, `#adventure` are not; a quiet channel ignores direct mentions.

## 12. Milestones

1. **Skeleton + Docker:** config loading, bot connects, replies "hi" on mention, runs via `docker compose up`. Verify: bot online, invalid config aborts startup, database persists across a container restart.
2. **Permissions + commands:** level resolution and gated slash commands. Verify: `test_permissions.py` passes, manual check in a test server.
3. **Chat with channel memory:** Haiku calls, history window, persona. Verify: multi-turn conversation stays in character and remembers earlier turns.
4. **Security layer:** sanitize, detect, output guard, directive channel. Verify: offline injection suite passes; live suite shows no canary leaks.
5. **Triggers + quiet channels:** unprompted replies, keywords, cooldown, quiet list commands. Verify: trigger and quiet-channel tests pass; manual check that `#vent` stays silent.
6. **Summaries + user facts:** background jobs, memory commands. Verify: facts appear in `/memory view`, injected facts are rejected.
7. **DM forwarding + owner replies.** Verify: DM from a second account arrives at the owner with attachments, Harmony answers it, and `/harmony dm reply` reaches the second account.
8. **Polish:** rate limits, status command, logging.

## 13. Open questions

1. **Persona details:** confirm the character notes in section 1, or supply your own character sheet for `persona/harmony.md`.
2. **Starter lists:** the default keywords and quiet-channel words in section 3 are guesses. Edit them before first run if you want different seeds.

## 14. adderall link

Harmony can read and change tasks in [adderall](https://github.com/bunniico/adderall) and DMs its owner about them. Code lives in `harmony/adderall/`; config is the optional `adderall` block.

- **Who:** only `adderall.user_id`, compared with `message.author.id` in code. Nobody else is offered the tools, so nobody else can reach adderall through Harmony.
- **Transport:** adderall's REST API over `httpx`. Its MCP server is not used because it only accepts requests addressed to `localhost`. Its alarm stream (`GET /api/events`, server-sent events) is read by a background task that reconnects with backoff.
- **Tools** (`tools.py`), run in a tool-use loop capped at 6 rounds:

| Kind | Tools | Runs |
|---|---|---|
| read | `list_projects`, `list_tasks`, `get_task` (every field of one task), `next_task`, `list_habits` | immediately |
| write | `add_task`, `start_task`, `check_habit` | immediately in the owner's DMs; behind Confirm in a server channel, where other people's messages are in the context |
| risky | `update_task`, `complete_task`, `delete_task`, `compile_braindump` | always behind Confirm |

- **Confirm:** a risky call is not run. Code records it and, after Harmony's reply, posts a message with Confirm/Cancel buttons. The text on it is written by code from the arguments, so what the owner approves is what runs. Only the owner's clicks count; prompts expire after 15 minutes; at most 5 per message. Task and routine ids must have come from an earlier list result, so a guessed id fails before any prompt is shown. `update_task` fields are checked against a whitelist in code.
- **Notifications** (DM to the owner, rewritten in Harmony's voice with a plain-text fallback, and saved to that DM's history): each transition alarm; actions taken from a server channel; a daily digest at `digest_time` in `timezone` (next task, overdue, due today, routines).
- **Times:** every timestamp in a read result is converted from adderall's UTC to `timezone`, with its offset, before the model sees it.
- **Tool results are data:** task titles are escaped and the context note tells the model they are never instructions.

