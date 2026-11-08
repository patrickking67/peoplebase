<div align="center">

# PeopleBase

**Employee data, clearly managed.**

A focused Flask and SQLAlchemy people operations workspace for managing, finding,
reporting on and exporting structured employee records.

[![CI](https://github.com/patrickking67/peoplebase/actions/workflows/ci.yml/badge.svg)](https://github.com/patrickking67/peoplebase/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/-Python%203.11+-3776AB?logo=python&logoColor=white&style=flat)
![Flask](https://img.shields.io/badge/-Flask-000000?logo=flask&logoColor=white&style=flat)
![SQLAlchemy](https://img.shields.io/badge/-SQLAlchemy-D71F00?logo=sqlalchemy&logoColor=white&style=flat)
![License](https://img.shields.io/badge/license-MIT-blue?style=flat)

**[patrickking67.github.io/peoplebase](https://patrickking67.github.io/peoplebase/)**

</div>

---

PeopleBase is a small, complete web app: a searchable employee directory with
payroll reporting, filtered CSV export and validated create, edit and delete
flows. It is deliberately compact, one application factory, one model and a
handful of templates, so the whole thing can be read in a sitting. The point is
the details a real people-data tool has to get right: CSRF protection, safe
spreadsheet export, strict input validation and a test suite that covers each
of them.

## Features

- **Directory search.** Case-insensitive search across name and position, an
  exact position filter, and paging (25 per page, `per_page` up to 100). LIKE
  wildcards in a search term are matched literally.
- **Reporting.** Matching record count, annual payroll and average salary,
  computed in SQL over every match, not just the visible page.
- **CSV export.** Exports every record that matches the current filters. Cells
  that start with `=`, `+`, `-`, `@`, tab or carriage return are prefixed with
  `'` so spreadsheet apps show them as text instead of running them as formulas.
- **Validated CRUD.** Required fields, 100-character limits, and salaries parsed
  as decimals between 0 and 999,999,999.99, rounded to cents. `NaN` and
  infinity are rejected.
- **CSRF protection.** Every POST form carries a per-session token, checked with
  a constant-time compare. Session cookies are `HttpOnly` and `SameSite=Lax`.
  Deletes are POST-only.
- **Config from the environment.** `SECRET_KEY` is required and never has a
  built-in default; `DATABASE_URL` points SQLAlchemy at any database.
- **Server-rendered UI.** Responsive, accessible Jinja templates with escaped
  output and no JavaScript framework.

## Quickstart

Requires Python 3.11 or newer.

With [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/patrickking67/peoplebase.git
cd peoplebase
uv venv
uv pip install -r requirements-dev.txt
source .venv/bin/activate
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
flask --app app run --debug
```

With pip:

```bash
git clone https://github.com/patrickking67/peoplebase.git
cd peoplebase
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
flask --app app run --debug
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000). The SQLite database is
created at `instance/employees.db`, which Git ignores. The `--debug` flag is for
local development only.

## Configuration

Settings come from environment variables. See [`.env.example`](.env.example);
PeopleBase does not load `.env` on its own, so export it with
`set -a; source .env; set +a`.

| Variable | Required | Purpose | Default |
| --- | --- | --- | --- |
| `SECRET_KEY` | Yes | Signs the session cookie, flash messages and CSRF tokens. The app refuses to start without it. | None |
| `DATABASE_URL` | No | SQLAlchemy database URL | SQLite at `instance/employees.db` |
| `FLASK_DEBUG` | No | `1` turns on the debugger when running `python app.py` | Off |

To serve it with a WSGI server, point the server at the factory, for example
`gunicorn "app:create_app()"`.

## Tests

```bash
pytest -q
python scripts/check-ai-config.py
```

The suite uses a fresh SQLite database per test and covers:

- CRUD flows, validation errors and HTML escaping (`tests/test_app.py`)
- search, filters, pagination, reporting totals and CSV export
  (`tests/test_directory.py`)
- CSRF enforcement, cookie flags, required `SECRET_KEY` and CSV formula
  neutralization (`tests/test_security.py`)

GitHub Actions runs the same commands on Python 3.11, 3.12 and 3.13 for every
pull request and every push to `main`.

## Project layout

```text
app.py                  Application factory, Employee model, routes, validation
templates/              Jinja templates (base, home, directory, add and edit forms)
static/styles.css       Stylesheet
tests/                  pytest suite and fixtures
docs/architecture.md    Design notes and production gaps
scripts/                Repository checks used in CI
site/                   Static GitHub Pages landing page
.github/workflows/      CI and Pages deployment
```

## Limits

PeopleBase has no login. Anyone who can reach it can read and change every
record, so do not deploy it publicly or load real employee data without adding
authentication and authorization first. See [architecture](docs/architecture.md)
and the [security policy](SECURITY.md).

## License

MIT, see [LICENSE](LICENSE).
