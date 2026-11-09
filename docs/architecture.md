# Architecture

PeopleBase is a server-rendered Flask application kept in one module,
`app.py`, so it can be read top to bottom.

## Parts

- `create_app()` owns configuration, the CSRF check, security headers, error
  handlers, routes and database setup. There is no module-level app instance,
  so importing `app.py` has no side effects; the Flask CLI and WSGI servers call
  the factory.
- Four SQLAlchemy models:
  - `Department`: a unique name.
  - `Employee`: name, job title, current salary, department, manager (a
    self-reference), status (`active`, `on_leave`, `terminated`) and start date.
  - `CompensationChange`: title and salary with an effective date and reason,
    one row per change.
  - `TimeOffRequest`: type, inclusive start and end dates, note, status
    (`pending`, `approved`, `denied`) and who decided it.
- Jinja templates with autoescaping, `static/styles.css` and a few lines of
  `static/app.js` for confirm-before-delete.

## Request handling

- Directory search builds one list of SQL filters (search, title, department,
  status) shared by the paged query, the summary query and the CSV export, so
  all three describe the same records.
- The page number is clamped to the last page using the summary count before
  the paged query runs, so out-of-range or huge values never reach SQLite.
- Searches use `icontains(..., autoescape=True)`, so input is a bound parameter
  and `%` or `_` match literally.
- The CSV export prefixes text cells that start with `=`, `+`, `-`, `@`, tab or
  carriage return with `'`, and is sent with `Cache-Control: no-store`.
- Unsafe methods require a CSRF token that matches the one in the signed
  session cookie, compared in constant time.
- Every response gets a Content-Security-Policy without `unsafe-inline`,
  `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, a Referrer-Policy
  and a Permissions-Policy. Bodies over 64 KiB get 413.
- Errors render `error.html`. A 500 rolls back the session and shows a generic
  message; the traceback goes only to the server log.

## Validation

All form input is checked in plain functions next to the routes:

- Text: required where needed, at most 100 characters (200 for time-off notes),
  no control characters.
- Salary: a plain ASCII number, finite, 0 to 999,999,999.99, rounded half-up to
  cents.
- Dates: `YYYY-MM-DD`, a real calendar date between 1900 and 2100.
- Ids: ASCII digits, at most nine of them, and the record must exist.
- Managers: not the employee, not someone who has left, and not anyone who
  already reports up to the employee (no loops).
- Time off: end on or after start, at most 60 days, no overlap with the same
  person's pending or approved time off.

## Time-off approval

There is no login, so the approver is picked on the decision form and checked
against the reporting line: the requester's manager if they have a current
one, otherwise any other current employee. Nobody decides their own request
and decided requests are final. This is a workflow rule for a trusted network,
not access control.

## Schema and upgrades

`db.create_all()` creates any missing tables on startup. It never alters an
existing table, so `upgrade_schema()` adds the employee columns that later
versions introduced (`department_id`, `manager_id`, `status`, `start_date`).
Each is nullable or has a default, so existing rows stay valid. A database
from the first release opens without a manual migration. A larger schema
change would justify Alembic; this one does not yet.

Deleting an employee deletes their compensation history and time-off requests,
clears `manager_id` on their reports and clears `decided_by_id` on requests
they decided. This is done in code because SQLite does not enforce foreign
keys by default.

## Configuration

`SECRET_KEY` must be set; the factory raises at startup without it. SQLite at
`instance/employees.db` is the default and `DATABASE_URL` points SQLAlchemy at
any other database. Tests create an isolated SQLite file per test. On
2026-11-09 the schema init, the upgrade of an original employee-only table and
the full test suite were also run once against PostgreSQL 18 (psycopg 3) with
no changes needed; CI still runs on SQLite only.

## Production gaps

PeopleBase is a portfolio and learning project, not a production HR system. A
real deployment holding employee information also needs authentication,
role-based authorization (who can see salaries, who can approve), encryption,
audit logging, backups, data-retention controls and proper migrations.
