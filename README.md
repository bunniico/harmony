# Harmony

An in-character Discord chatbot playing Harmony from Splatoon 3, backed by Claude Haiku. See `PLAN.md` for the design.

## Setup

1. Create a Discord application and bot. Under **Bot**, enable the **Message Content** intent. Invite it with the `bot` and `applications.commands` scopes and the Send Messages, Read Message History, and Embed Links permissions.
2. `cp config.example.json config.json` and put your Discord user ID in `owner_ids`.
3. `cp .env.example .env` and fill in `DISCORD_TOKEN` and `ANTHROPIC_API_KEY`.
4. `docker compose up -d --build`, then `docker compose logs -f harmony`.

Slash commands are synced globally on startup. Discord can take a while to show new commands the first time.

## adderall link

Harmony can manage tasks in [adderall](https://github.com/bunniico/adderall) for one person and DM them about it. Set the `adderall` block in `config.json`:

| Key | Meaning |
|---|---|
| `enabled` | Turn the link on or off. Leave the whole block out to disable it too. |
| `url` | Where adderall answers. The default `http://host.docker.internal:8000` reaches adderall's published port on the same machine. |
| `user_id` | The only Discord user who can use it, and who gets the DMs. |
| `timezone` | IANA name, e.g. `Europe/London`. Used for deadlines and the digest. |
| `digest_time` | `HH:MM` for the morning digest, or `null` to turn it off. |
| `alarms` | DM each stop / get ready / go alarm. |
| `webhook_port` | Port to receive adderall's webhooks on (e.g. `8081`), or `null` for off. Needs `ADDERALL_WEBHOOK_TOKEN` in `.env`. |

That person can ask Harmony, in a DM or by mentioning her, things like "what's next?", "add call the dentist tomorrow at 10", or "delete the laundry task". Reading, adding, starting and ticking routines happen straight away in DMs. Editing, completing, deleting and compiling a braindump always wait for a **Confirm** button, and in a server channel every change does. Harmony must be able to DM that user (they share a server and DMs are open).

### ClickUp assignments

adderall can announce each ClickUp task newly assigned to you. To have Harmony DM them to you:

1. Make a token: `python -c "import secrets; print(secrets.token_urlsafe(32))"`, and put it in `.env` as `ADDERALL_WEBHOOK_TOKEN=...`.
2. Set `"webhook_port": 8081` in the `adderall` block. `docker-compose.yml` already publishes 8081.
3. Restart Harmony: `docker compose up -d --build`.
4. In adderall, ⚙ Settings → ClickUp sync → *New assignment webhooks*, add `http://host.docker.internal:8081/adderall/<your token>`.

The token is the only thing protecting that URL, so keep it secret. A request with the wrong one gets a 404.

adderall has no login, so anything that can reach its port can change your tasks. Keep it off networks you don't trust.

## Updating

- Code: `git pull && docker compose up -d --build`
- Persona: edit `persona/harmony.md` or `persona/rules.md`, then run `/harmony persona reload`. No rebuild needed.

## Starting on boot

`start.sh` pulls the latest `main` (fast-forward only), then runs `docker compose up -d --build`. If the pull fails, for example because the network isn't up yet, it logs a warning and starts with the code already checked out.

To run it at boot with systemd:

1. Edit `harmony.service`: set `User` to the account that owns the clone (it needs git access and must be in the `docker` group) and set `ExecStart` to the full path of `start.sh`.
2. `sudo cp harmony.service /etc/systemd/system/`
3. `sudo systemctl daemon-reload && sudo systemctl enable --now harmony`

Check the output with `journalctl -u harmony`.

## Running without Docker

```
pip install -r requirements-dev.txt
python -m harmony
```

Environment variables: `HARMONY_CONFIG` (default `config.json`), `HARMONY_PERSONA_DIR` (default `persona`), `HARMONY_DB` (default `harmony.db`), `LOG_LEVEL`.

## Tests

```
pytest                                   # offline suite
HARMONY_LIVE_TESTS=1 pytest -m live      # runs the injection fixtures against the real model
```
