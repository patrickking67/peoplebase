# AGENTS.md

Guide for coding agents working in PeopleBase. `CLAUDE.md` and
`.github/copilot-instructions.md` point here.

## What this is

A small HRIS: people records, pay history, time off with approval and leave
balances, onboarding and offboarding checklists, policy acknowledgements, a
headcount report and an audit log. Three roles (employee, manager, HR admin)
sign in and see only what their role and reporting line allow. Flask 3,
Flask-SQLAlchemy, Alembic through Flask-Migrate, Argon2id passwords,
server-rendered Jinja, SQLite by default. A static project page lives in
`site/` and deploys to GitHub Pages.

## Layout

| Path | What it holds |
| --- | --- |
| `app.py` | `create_app()`, the `before_request` gates, every route, validation helpers, `audit()`, the `seed-demo` and `create-admin` commands |
| `access.py` | `Access`: the only place that decides what a role may see or do. `reporting_tree()` |
| `models.py` | Models, constants, checklist templates, the audit action registry and the hooks that make `AuditEvent` append-only |
| `leave.py`, `reports.py` | Pure functions for leave balances and for headcount and attrition. No Flask or database imports |
| `migrations/` | Alembic environment and `versions/000N_*.py` |
| `templates/` | Jinja. `base.html` is the layout; partials start with `_` |
| `static/` | `styles.css` (design tokens at the top), `app.js` (confirm before destructive submits), brand assets |
| `tests/` | pytest. `conftest.py` has the fixtures; `legacy_schema.py` freezes old schemas for migration tests |
| `scripts/check.sh` | The single check command |
| `scripts/dev.sh` | Dev server on port 4104 with a fake `SECRET_KEY`; `--demo` loads demo data |
| `site/` | Project page, `llms.txt`, `social-preview.png`, screenshots in `site/assets/` |
| `docs/` | `architecture.md` (diagrams and design) and `security.md` (threat model) |
| `.claude/launch.json` | Preview configs: `peoplebase-app` (4104) and `peoplebase-site` (4114) |

## Commands

Run everything from the repository root.

```bash
python3 -m venv .venv                         # once; .venv is gitignored
.venv/bin/pip install -r requirements-dev.txt
./scripts/check.sh                            # ruff, pytest, repo checks; must pass before every commit
.venv/bin/pytest -q tests/test_access.py      # one file while iterating
.venv/bin/ruff check . --fix                  # lint autofix
```

Run and preview:

```bash
./scripts/dev.sh --demo                               # migrate, load demo data if empty, serve http://127.0.0.1:4104
./scripts/dev.sh                                      # the same without demo data
SECRET_KEY=dev-only-not-secret .venv/bin/flask --app app create-admin   # first HR admin on a real install
python3 -m http.server 4114 --directory site          # project page at http://127.0.0.1:4114
```

`seed-demo` prints three sign-ins and one random password for that run. It is
not stored anywhere, so reseed (delete `instance/employees.db`) if you lose it.

Migrations:

```bash
SECRET_KEY=dev-only-not-secret .venv/bin/flask --app app db upgrade     # apply
SECRET_KEY=dev-only-not-secret .venv/bin/flask --app app db downgrade   # undo one revision
TEST_POSTGRES_URL="postgresql+psycopg:///peoplebase_migrations_test" .venv/bin/pytest -q tests/test_migrations.py
```

The PostgreSQL run needs `pip install "psycopg[binary]"` and a throwaway
database whose name contains `test`; the tests empty it.

## Conventions

- Keep the factory pattern: no module-level app, and importing `app.py` has no
  side effects.
- **Access.** Every new route gets a row in `ROUTES` in `tests/test_access.py`
  (a test fails until it does). Load the record, then ask `g.access`; never
  compare `role` strings in a route or template. Out of reach is `abort(404)`,
  a role that may never do the action is 403 (`@admin_only`).
- **Identity comes from the session.** Never read the acting person, the
  approver, a role or an owner id from a form field.
- **Audit.** Every change to people, pay, roles, accounts, time off,
  checklists or policies calls `audit()` before the commit. New actions go in
  `AUDIT_ACTIONS` in `models.py`. Never log passwords or hashes.
- **Schema.** Change the model and add `migrations/versions/000N_name.py` by
  hand in the same commit, with a working `downgrade()`. Use
  `op.batch_alter_table` to alter existing tables. `tests/test_migrations.py`
  fails if the migrations and models disagree. Nothing creates tables at
  startup.
- Every POST form includes `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">`.
  State changes are POST-only.
- Validate at the boundary with the existing helpers (`clean_text`,
  `parse_date`, `parse_id`, `is_small_id`, `validate_*`). Return a message,
  flash it and re-render with 400.
- No inline `<script>`, `on*=` handlers or `style=` attributes: the CSP forbids
  them.
- CSV columns are a stable contract; tests pin them. Run text cells through
  `neutralize_formula()`. An export uses the same scoped query as its page.
- Rules with arithmetic (accrual, headcount) go in a pure module and get
  table-driven tests.
- Copy: sentence case, plain verbs, no em dashes, no emoji. `check-text.py`
  enforces the last two.
- Demo and test people are fake and named "<first> Example". Test accounts
  use `@peoplebase.test`, demo accounts `@peoplebase.example`.
- Conventional Commits, one logical change per commit, and a line in
  `CHANGELOG.md` under `[Unreleased]`.

## Safety rules

- Never commit `.env`, `instance/`, databases, `.venv/` or credentials. The
  only committed key is the public `dev-only-not-secret` in `scripts/dev.sh`,
  and the only committed password is the test-only one in `tests/conftest.py`.
- No real employee data anywhere, including fixtures and screenshots.
- Do not weaken sign-in, the session checks, lockout, CSRF, cookie flags,
  security headers, `no-store`, the request size limit or the required
  `SECRET_KEY`. Do not add a default `SECRET_KEY` or a default account.
- Do not add a code path that updates or deletes `AuditEvent` rows.
- Read `docs/security.md` before touching `access.py` or anything in the
  sign-in flow, and add a test for any new abuse case.
- Do not edit `LICENSE`, repository settings or the Pages workflow's
  permissions.

## Definition of done

1. `./scripts/check.sh` passes.
2. New or changed behavior has tests, including the refusals.
3. If the UI changed, you looked at it in the browser at desktop width and at
   375px (no sideways scroll) in light and dark mode, as each role it affects.
4. README, this file, `site/index.html`, `llms.txt`, `docs/` and
   `CHANGELOG.md` match the change. `check-docs.py` checks the test count.
