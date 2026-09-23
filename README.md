# Harmony

An in-character Discord chatbot playing Harmony from Splatoon 3, backed by Claude Haiku. See `PLAN.md` for the design.

## Setup

1. Create a Discord application and bot. Under **Bot**, enable the **Message Content** intent. Invite it with the `bot` and `applications.commands` scopes and the Send Messages, Read Message History, and Embed Links permissions.
2. `cp config.example.json config.json` and put your Discord user ID in `owner_ids`.
3. `cp .env.example .env` and fill in `DISCORD_TOKEN` and `ANTHROPIC_API_KEY`.
4. `docker compose up -d --build`, then `docker compose logs -f harmony`.

Slash commands are synced globally on startup. Discord can take a while to show new commands the first time.

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
