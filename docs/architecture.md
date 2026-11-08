# Architecture

PeopleBase is a server-rendered Flask application with three layers kept intentionally close together for readability.

- `create_app()` owns configuration, CSRF checks, database setup, and route registration. There is no module-level app instance, so importing `app.py` has no side effects; the Flask CLI and WSGI servers call the factory.
- `Employee` defines the SQLAlchemy persistence model.
- Jinja templates and `static/styles.css` provide the interface.

## Request handling

- Directory search builds one list of SQL filters that is shared by the paged result query, the summary query (count, payroll sum, average), and the CSV export, so all three always describe the same set of records.
- Searches use SQLAlchemy `icontains(..., autoescape=True)`, so user input is a bound parameter and `%` or `_` in a search term match literally.
- The CSV export writes every matching row and prefixes any text cell that starts with `=`, `+`, `-`, `@`, tab, or carriage return with `'` to stop formula injection when the file is opened in a spreadsheet.
- Unsafe methods (POST, PUT, PATCH, DELETE) require a CSRF token that matches the one stored in the signed session cookie. Templates get the token from the `csrf_token()` global.
- Salaries are parsed as `Decimal`, rejected if not finite or out of range, and rounded half-up to cents.

## Configuration

`SECRET_KEY` must be set in the environment; the factory raises at startup if it is missing. SQLite is the zero-configuration default database. `DATABASE_URL` can point SQLAlchemy at a production database without changing application code. Tests create an isolated SQLite database for every test.

## Production gaps

The application is a portfolio and learning project, not a production HR system. A real deployment holding employee information also needs authentication, role-based authorization, encryption, audit logging, backup policy, and data-retention controls.
