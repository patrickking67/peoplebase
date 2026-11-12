#!/usr/bin/env bash
# Run PeopleBase locally on port 4104 with a fake development SECRET_KEY.
# The key below is public and only for local use; never deploy with it.
#
#   ./scripts/dev.sh          create or update the tables, then serve
#   ./scripts/dev.sh --demo   also load the fake demo company if the database is empty
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/flask ]; then
  echo "No virtualenv found. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt" >&2
  exit 1
fi
export SECRET_KEY="${SECRET_KEY:-dev-only-not-secret}"
# Safe to run every time: it only applies revisions the database does not have.
.venv/bin/flask --app app db upgrade
if [ "${1:-}" = "--demo" ]; then
  # seed-demo refuses a database that already has people, which is fine here.
  .venv/bin/flask --app app seed-demo || echo "Keeping the data that is already there."
fi
exec .venv/bin/flask --app app run --port "${PORT:-4104}"
