#!/usr/bin/env sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_dir=$(dirname -- "$script_dir")
cd "$project_dir"

if [ ! -x ./.venv/bin/python ] || [ ! -d ./node_modules ]; then
  echo "Setup is incomplete. Run bash scripts/setup.sh first." >&2
  exit 1
fi

exec npm run local
