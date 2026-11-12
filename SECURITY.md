# Security policy

## Supported versions

PeopleBase has no tagged releases. Security fixes go to the latest commit on
`main`; older commits are not patched.

## Reporting a vulnerability

Report privately through GitHub security advisories:
https://github.com/patrickking67/peoplebase/security/advisories/new

Do not open a public issue or pull request with exploit details, credentials
or personal data. Include the commit you tested, steps to reproduce and the
impact you see.

I aim to acknowledge a report within 7 days and to fix confirmed issues, or
explain why not, within 30 days. This is a personal project, so these are
targets rather than guarantees.

## Scope

In scope: the Flask application in this repository and the project page in
`site/`. The security model, with the access rules and the abuse cases the
code is tested against, is in [docs/security.md](docs/security.md). Examples
of what to report:

- Reading or changing a record outside your role or reporting line, including
  through a CSV export.
- Seeing someone else's pay as a manager or employee.
- Signing in without the password, getting around the lockout, or keeping a
  session alive after sign-out, a password change or deactivation.
- Changing your own role, or deciding your own time off.
- Changing or deleting an audit entry through the app.
- CSRF bypass, cross-site scripting, CSV formula injection, header or CSP
  gaps, and error pages that leak internals.

Out of scope: the limits listed under
[What this does not cover](docs/security.md#what-this-does-not-cover), such as
the absence of multi-factor authentication, and issues in Flask, SQLAlchemy,
Alembic, argon2-cffi or other dependencies, which belong with those projects.

## Deployment notes

- PeopleBase is a portfolio project. Read the limits above before putting real
  employee data in it.
- `SECRET_KEY` is required in every environment. Use a long random value and
  keep it out of source control. Changing it signs everyone out. The
  `dev-only-not-secret` key in `scripts/dev.sh` is public and for local use
  only.
- Set `SESSION_COOKIE_SECURE=1` and terminate TLS at the hosting platform.
- Create the first HR admin with `flask --app app create-admin`. There is no
  default account. Never run `seed-demo` against a real database; it refuses
  one that already has people.
- Use a managed database through `DATABASE_URL`, and run
  `flask --app app db upgrade` on every deploy.
- Never enable Flask debug mode on a reachable host.
