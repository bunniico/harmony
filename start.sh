#!/usr/bin/env bash
# Pull the latest main, then build and start the bot.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

if ! git pull --ff-only origin main; then
    echo "start.sh: git pull failed, starting with the current checkout" >&2
fi

docker compose up -d --build
