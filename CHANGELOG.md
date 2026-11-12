# Changelog

All notable changes to PeopleBase are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). There are no tagged releases yet;
earlier entries are reconstructed from the Git history.

## [Unreleased]

### Added

- Sign-in with three roles: employee, manager and HR admin. Passwords are
  hashed with Argon2id (argon2-cffi). Sessions are cleared at sign-in, can be
  revoked through a per-account nonce and end after 12 hours.
- Lockout: five failed sign-ins lock an email for 15 minutes and twenty lock
  an address, without revealing which emails have accounts.
- Server-side access control on every route through one module, `access.py`.
  Employees see their own record, pay and time off; managers see their direct
  and indirect reports without pay; HR sees everything. Records out of reach
  answer 404.
- Account management for HR: create accounts with a one-time temporary
  password, change roles, reset passwords, turn accounts off. A
  `create-admin` command for the first HR admin.
- An append-only audit log of changes to people, pay, roles, accounts,
  departments, time off, checklists, policies and exports, readable by HR.
- A last working day on the employee record, and onboarding and offboarding
  checklists. Marking someone as left turns off their account.
- Policies with immutable text and per-person acknowledgements.
- Leave balances by type with monthly accrual, in working days.
- A headcount and attrition report with a CSV limited to the viewer's
  reporting tree.
- Versioned schema migrations with Alembic through Flask-Migrate, tested from
  every earlier schema shape on SQLite and PostgreSQL.
- `docs/security.md`: trust boundaries, access rules and 16 abuse cases, each
  with a test.
- Departments (add, rename, delete), reporting lines with an org chart,
  employment status, compensation history, time-off requests, a people
  dashboard and `seed-demo` demo data.
- Security headers, a 64 KiB request limit and error pages in the app layout.
- `scripts/check.sh`, `scripts/dev.sh`, `.claude/launch.json`, `llms.txt`,
  CONTRIBUTING, CODEOWNERS, Dependabot, issue and PR templates.
- A check that fails when a document quotes the wrong test count.

### Changed

- Time off is decided by the signed-in account. The form no longer names an
  approver, and anyone above the requester in the reporting line, or HR, can
  decide.
- The directory shows salary, status and people who left to HR only. The
  directory CSV is HR-only.
- `seed-demo` also creates demo accounts at `@peoplebase.example` and prints
  one random password for the run.
- Every page served to a signed-in account is `Cache-Control: no-store`.
- Models moved to `models.py`. The app no longer creates or patches tables at
  startup: run `flask --app app db upgrade`.
- README, architecture notes and the project page rewritten around the access
  model, with diagrams and new screenshots.

### Fixed

- A very large `page` number returned a 500 error (SQLite integer overflow).
- Names and titles with tabs, line breaks or NUL characters were accepted.
- Salaries such as `1_000` or non-ASCII digits were accepted.

## [0.3.0] - 2026-11-07

### Added

- CSRF tokens on every form, `SameSite=Lax` cookies and a required
  `SECRET_KEY`.
- CSV formula neutralization.
- Configuration, security model and quickstart documentation.

## [0.2.0] - 2026-08-25

### Added

- GitHub Pages landing page.

## [0.1.0] - 2026-07-13

### Added

- Rebuilt Flask and SQLAlchemy app with an application factory, validated
  create, edit and delete, search, payroll reporting and CSV export.

[Unreleased]: https://github.com/patrickking67/peoplebase/compare/main...HEAD
[0.3.0]: https://github.com/patrickking67/peoplebase/commit/cd018df
[0.2.0]: https://github.com/patrickking67/peoplebase/commit/d9a6c9b
[0.1.0]: https://github.com/patrickking67/peoplebase/commit/ed40fe7
