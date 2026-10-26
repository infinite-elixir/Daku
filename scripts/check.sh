#!/usr/bin/env bash
# The single check command: lint, types, tests. Pre-commit and CI both call this.
#   scripts/check.sh          backend only
#   scripts/check.sh --web    also run the SPA tests
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -q

if [[ "${1:-}" == "--web" ]]; then
  pnpm -C web test
fi
