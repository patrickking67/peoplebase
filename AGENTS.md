# AGENTS.md

Guide for coding agents working in PeopleBase. `CLAUDE.md` and
`.github/copilot-instructions.md` point here.

## What this is

A small HR system: employee records, departments, reporting lines,
compensation history, time off with manager approval, a dashboard and a CSV
export. Flask 3 and Flask-SQLAlchemy, server-rendered Jinja, SQLite by
default. No login. A static landing page lives in `site/` and deploys to
GitHub Pages.

## Layout

| Path | What it holds |
| --- | --- |
| `app.py` | Everything server-side: `create_app()`, models (`Department`, `Employee`, `CompensationChange`, `TimeOffRequest`), routes, validation helpers, `upgrade_schema()`, the `seed-demo` CLI command |
| `templates/` | Jinja templates. `base.html` is the layout; `_employee_form.html` is shared by add and edit |
| `static/` | `styles.css` (design tokens at the top), `app.js` (confirm-before-delete only), `brand/` logo and mark, favicons |
| `tests/` | pytest. `conftest.py` gives `app`, `client`, `csrf_token`, `add_employees`; one fresh SQLite file per test |
| `scripts/check.sh` | The single check command |
| `scripts/dev.sh` | Dev server on port 4104 with a fake `SECRET_KEY` |
| `scripts/check-ai-config.py`, `scripts/check-text.py` | Repo checks run by `check.sh` |
| `site/` | GitHub Pages landing page, `llms.txt`, social preview (`social-preview.svg` is the source of the PNG) |
| `docs/architecture.md` | Design notes and production gaps |
| `.claude/launch.json` | Preview configs: `peoplebase-app` (4104) and `peoplebase-site` (4114) |

## Commands

Run everything from the repository root.

```bash
python3 -m venv .venv                         # once; .venv is gitignored
.venv/bin/pip install -r requirements-dev.txt
./scripts/check.sh                            # ruff, pytest, repo checks; must pass before every commit
.venv/bin/pytest -q tests/test_time_off.py    # one file while iterating
.venv/bin/ruff check . --fix                  # lint autofix
```

Run and preview:

```bash
SECRET_KEY=dev-only-not-secret .venv/bin/flask --app app seed-demo   # demo data, empty database only
./scripts/dev.sh                                                     # http://127.0.0.1:4104
python3 -m http.server 4114 --directory site                         # landing page at http://127.0.0.1:4114
```

In the Claude desktop app, start the same two servers by name from
`.claude/launch.json`. The dev database is `instance/employees.db`; delete it to
start over.

## Conventions

- Keep the factory pattern: no module-level app, and importing `app.py` has no
  side effects.
- Every POST form includes `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">`.
  Deletes and decisions are POST-only.
- Validate at the boundary with the existing helpers: `validate_employee`,
  `validate_employment`, `clean_text` (length and control characters),
  `parse_date` (YYYY-MM-DD, 1900 to 2100), `parse_id` and `is_small_id` (ids the
  database can compare). Return a message, flash it and re-render with 400.
- New columns on an existing table must be nullable or defaulted and listed in
  `EMPLOYEE_UPGRADE_COLUMNS` (or an equivalent for that table) so
  `upgrade_schema()` adds them to old databases. New tables need nothing extra.
- No inline `<script>`, `on*=` handlers or `style=` attributes: the CSP forbids
  them. Put behavior in `static/app.js` and styling in `static/styles.css`.
- `/employees.csv` columns are a stable contract; tests pin them. Run text
  cells through `neutralize_formula()`.
- Some existing tests pin markup (for example the summary grid's
  `<strong>N</strong><span>Label</span>`). Change markup and tests together.
- Copy: sentence case, plain verbs, no em dashes, no emoji. `check-text.py`
  enforces the last two.
- Demo and test people are fake and named "<first> Example", "Sample" or
  "Placeholder".
- Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`, `style:`, `test:`),
  one logical change per commit, and add a line to `CHANGELOG.md` under
  `[Unreleased]`.

## Safety rules

- Never commit `.env`, `instance/`, databases, `.venv/` or credentials. The
  only committed key is the public `dev-only-not-secret` in `scripts/dev.sh`.
- No real employee data anywhere, including fixtures and screenshots.
- Do not weaken CSRF, cookie flags, security headers, the request size limit or
  the required `SECRET_KEY`. Do not add a default `SECRET_KEY` to `app.py`.
- The time-off approver check is a workflow rule, not authentication. Do not
  describe it as access control.
- Do not edit `LICENSE`, repository settings or the Pages workflow's
  permissions.

## Definition of done

1. `./scripts/check.sh` passes.
2. New or changed behavior has tests, including the failure paths.
3. If the UI changed, you looked at it in the browser at desktop width and at
   375px (no sideways scroll) in light and dark mode.
4. README, this file, `site/index.html` (including the test count) and
   `CHANGELOG.md` match the change.
