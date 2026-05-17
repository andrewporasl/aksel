#!/usr/bin/env bash
set -euo pipefail

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "Python was not found. Install Python 3.10 or newer, then run this script again."
  exit 1
fi

"$PYTHON_BIN" -m venv .venv

if [[ -f ".venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
else
  echo "Virtual environment was created, but the activation script was not found."
  exit 1
fi

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "Warning: ffmpeg was not found on PATH. Install ffmpeg before running media commands."
else
  echo "ffmpeg found."
fi

if [[ ! -f ".env" ]]; then
  cp ".env.example" ".env"
  echo "Created .env from .env.example."
fi

echo "Setup complete. Edit .env with your Discord bot token, then run: bash launch.sh"
