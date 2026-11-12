#!/usr/bin/env bash
# Run PeopleBase locally on port 4104 with a fake development SECRET_KEY.
# The key below is public and only for local use; never deploy with it.
# Load demo data first with: SECRET_KEY=dev-only-not-secret .venv/bin/flask --app app seed-demo
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/flask ]; then
  echo "No virtualenv found. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt" >&2
  exit 1
fi
export SECRET_KEY="${SECRET_KEY:-dev-only-not-secret}"
# Create or update the tables. Safe to run every time.
.venv/bin/flask --app app db upgrade
exec .venv/bin/flask --app app run --port "${PORT:-4104}"
