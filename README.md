<div align="center">

# PeopleBase

**Employee data, clearly managed.**

A focused Flask and SQLAlchemy people operations workspace for managing, finding,
and exporting structured employee records.

![Python](https://img.shields.io/badge/-Python%203.9+-3776AB?logo=python&logoColor=white&style=flat)
![Flask](https://img.shields.io/badge/-Flask-000000?logo=flask&logoColor=white&style=flat)
![SQLAlchemy](https://img.shields.io/badge/-SQLAlchemy-D71F00?logo=sqlalchemy&logoColor=white&style=flat)
![Tests](https://img.shields.io/badge/tests-10%20passing-3f6f4a?style=flat)
![License](https://img.shields.io/badge/license-MIT-blue?style=flat)

**[patrickking67.github.io/peoplebase](https://patrickking67.github.io/peoplebase/)**

</div>

---

## What it demonstrates

- Application-factory architecture and environment-based configuration
- Create, read, update, and delete workflows
- Name and position search with role-based filtering
- Workforce summaries and filtered CSV exports
- Server-side validation with useful error states
- POST-only destructive actions
- Responsive, accessible server-rendered templates
- Isolated integration tests across supported Python versions

## Run locally

```bash
git clone https://github.com/patrickking67/peoplebase.git
cd peoplebase
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
flask --app app run --debug
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000). The SQLite database is created under `instance/` and is ignored by Git.

## Verify

```bash
pytest -q
python scripts/check-ai-config.py
```

## Configuration

| Variable | Purpose | Default |
| --- | --- | --- |
| `DATABASE_URL` | SQLAlchemy database connection | Local SQLite |
| `SECRET_KEY` | Session and flash-message signing | Development-only value |
| `FLASK_DEBUG` | Enables debug mode when set to `1` | Disabled |

Set a unique `SECRET_KEY` and add authentication before any public deployment. See [architecture](docs/architecture.md) and [security policy](SECURITY.md).

## License

MIT © Patrick King
