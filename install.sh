#!/usr/bin/env sh
set -eu
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install .
if [ -f .env ]; then
  chmod 600 .env
  echo "Installed. Existing .env kept with owner-only permissions. Run: .venv/bin/syncbridge serve"
else
  echo "Installed. Run: .venv/bin/syncbridge init"
  echo "Then start SyncBridge with: .venv/bin/syncbridge serve"
fi
