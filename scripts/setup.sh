#!/usr/bin/env sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_dir=$(dirname -- "$script_dir")
cd "$project_dir"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3.11 or newer is required." >&2
  exit 1
fi

python3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 'Python 3.11 or newer is required.')"

if ! command -v npm >/dev/null 2>&1; then
  echo "Node.js 22 and npm are required." >&2
  exit 1
fi

python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -e . pytest httpx2
npm ci

printf '\nMirdain Print Hub is ready. Start it with: npm run local\n'
