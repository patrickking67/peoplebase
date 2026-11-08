# Contributing

Thanks for helping. PeopleBase stays small on purpose, so changes should keep
the code readable in one sitting.

## Setup

```bash
git clone https://github.com/patrickking67/peoplebase.git
cd peoplebase
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
./scripts/check.sh
```

Run the app with `./scripts/dev.sh` (port 4104, fake development key) after
loading demo data with
`SECRET_KEY=dev-only-not-secret .venv/bin/flask --app app seed-demo`.

## Branches

Branch from `main` and name the branch after the change:

- `feat/<short-name>` for new behavior
- `fix/<short-name>` for bug fixes
- `docs/<short-name>` for documentation only
- `chore/<short-name>` for tooling, CI and dependencies

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/): `feat:`,
`fix:`, `docs:`, `chore:`, `style:`, `test:`, with an optional scope such as
`feat(time-off):`. One logical change per commit, and every commit should pass
`./scripts/check.sh`.

## Pull request checklist

- `./scripts/check.sh` passes locally.
- New or changed behavior has tests, including the error paths.
- UI changes were checked at desktop width and at 375px, in light and dark mode.
- README, `AGENTS.md` and `CHANGELOG.md` (`[Unreleased]`) are updated.
- No secrets, `.env` files or real employee data.

Report security problems privately through
[GitHub security advisories](https://github.com/patrickking67/peoplebase/security/advisories/new),
not in issues or pull requests.
