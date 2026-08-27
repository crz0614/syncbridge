#!/usr/bin/env sh
set -eu
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install .
if [ ! -f .env ]; then cp .env.example .env; fi
echo "Installed. Edit .env, then run: .venv/bin/syncbridge serve"
