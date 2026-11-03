# Repository instructions

PeopleBase is a focused Flask and SQLAlchemy CRUD application. Keep the application factory in `app.py`, preserve POST-only deletion, validate all submitted employee data, and add tests for behavior changes.

## Commands

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
python scripts/check-ai-config.py
flask --app app run --debug
```

The dev server defaults to http://127.0.0.1:5000. On macOS, AirPlay Receiver often holds port 5000, so pass `--port 5001`. The SQLite database is created at `instance/employees.db` and is gitignored.

There is no linter or formatter configured.

## Layout

- `app.py`: `create_app()`, the `Employee` model, and all routes
- `templates/`, `static/styles.css`: server-rendered UI
- `tests/`: pytest integration tests; `conftest.py` gives each test an isolated SQLite database
- `site/`: static landing page published to GitHub Pages
- `scripts/check-ai-config.py`: fails unless `AGENTS.md`, `CLAUDE.md` and `.github/copilot-instructions.md` exist and `CLAUDE.md` is exactly `@AGENTS.md`. Put shared instructions here, not in `CLAUDE.md`.

## CI and deploys

- `ci.yml` runs `pytest -q` and `check-ai-config.py` on Python 3.11, 3.12 and 3.13 for pushes to `main` and every PR.
- `pages.yml` publishes `site/` to GitHub Pages on pushes to `main` that touch `site/`. The Flask app itself is not deployed anywhere.

Do not commit databases, virtual environments, environment files, user-local agent state, or credentials.
