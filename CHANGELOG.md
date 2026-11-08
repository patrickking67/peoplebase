# Changelog

All notable changes to PeopleBase are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). There are no tagged releases yet;
earlier entries are reconstructed from the Git history.

## [Unreleased]

### Added

- Departments with headcount, and filters for department and status in the
  directory and CSV export.
- Reporting lines: a manager per employee, checked for self-management, loops
  and managers who have left, plus an org chart page.
- Employment status (active, on leave, left) and start dates.
- Compensation history with effective dates and reasons, shown on a new
  employee profile page.
- Time-off requests with overlap checks and a manager approval rule.
- A people dashboard: who is at work, headcount by department, upcoming work
  anniversaries, who is out today and pending requests.
- `flask --app app seed-demo` for clearly fake demo data.
- In-place schema upgrade for databases created by earlier versions.
- Security headers (CSP without inline script, nosniff, frame denial,
  Referrer-Policy, Permissions-Policy), a 64 KiB request limit and
  `Cache-Control: no-store` on the CSV export.
- Error pages in the app layout for 400, 404, 405, 413 and 500.
- New logo, mark and favicon set, a redesigned UI with light and dark themes,
  and a rebuilt landing page with a regenerated social preview.
- `scripts/check.sh` (ruff, pytest, repo checks), `scripts/dev.sh`,
  `.claude/launch.json`, `llms.txt`, CONTRIBUTING, CODEOWNERS, Dependabot,
  issue and PR templates, `.editorconfig` and `.gitattributes`.

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
