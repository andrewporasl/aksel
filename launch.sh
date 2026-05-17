#!/usr/bin/env bash
set -euo pipefail

if [[ -f ".venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

if [[ ! -f ".env" ]]; then
  echo ".env was not found. Run bash setup.sh, then add your Discord bot token to .env."
  exit 1
fi

if ! grep -Eq '^DISCORD_TOKEN=.+$' ".env" || grep -Eq '^DISCORD_TOKEN=your_bot_token_here$' ".env"; then
  echo "DISCORD_TOKEN is missing or still set to the placeholder in .env."
  exit 1
fi

python bot.py
