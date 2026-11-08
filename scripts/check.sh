#!/usr/bin/env bash
# The one command to run before every commit and in CI: lint, tests, repo checks.
# Usage: ./scripts/check.sh   (uses .venv/bin if present, otherwise the active Python)
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -x .venv/bin/python ]; then
  PATH="$PWD/.venv/bin:$PATH"
fi

echo "==> ruff"
ruff check .
echo "==> pytest"
pytest -q
echo "==> repository checks"
python scripts/check-ai-config.py
python scripts/check-text.py
echo "==> all checks passed"
