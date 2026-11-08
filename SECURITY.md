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

In scope: the Flask application in this repository (`app.py`, `templates/`,
`static/`) and the landing page in `site/`. Examples: CSRF bypass, cross-site
scripting, CSV formula injection, header or CSP gaps, validation bypasses that
store bad data, and error pages that leak internals.

Out of scope: the absence of authentication, and the time-off approver being
chosen on the form. Both are documented limits, not vulnerabilities. Issues in
Flask, SQLAlchemy or other dependencies belong with those projects.

## Deployment notes

- PeopleBase has no login. Anyone who can reach it can read and change every
  record. Keep it on a private network and use demo data unless you add
  authentication and authorization.
- `SECRET_KEY` is required in every environment. Use a long random value and
  keep it out of source control. The `dev-only-not-secret` key in
  `scripts/dev.sh` is public and for local use only.
- Use a managed database through `DATABASE_URL` for anything beyond a demo.
- Terminate TLS at the hosting platform and never enable Flask debug mode on a
  reachable host.
