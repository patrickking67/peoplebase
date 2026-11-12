"""The versioned migrations, run for real against SQLite and, when
TEST_POSTGRES_URL points at a throwaway database, PostgreSQL.

Every other test builds its schema with db.create_all(), so the first test
here is the one that keeps that honest: after "db upgrade" the database must
match the models exactly.
"""

import os
from datetime import date, datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from flask_migrate import downgrade, upgrade

from app import MIGRATIONS_DIRECTORY, Employee, create_app, db
from tests import legacy_schema

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "")
BACKENDS = ["sqlite"] + (["postgresql"] if POSTGRES_URL else [])


def reset_postgres(url):
    """Empty the scratch database. Refuses anything not named like a test database."""
    database = sa.engine.make_url(url).database or ""
    if "test" not in database:
        raise RuntimeError("TEST_POSTGRES_URL must name a throwaway database with 'test' in its name.")
    engine = sa.create_engine(url)
    with engine.begin() as connection:
        connection.execute(sa.text("DROP SCHEMA public CASCADE"))
        connection.execute(sa.text("CREATE SCHEMA public"))
    engine.dispose()


@pytest.fixture(params=BACKENDS)
def database_url(request, tmp_path):
    if request.param == "sqlite":
        yield f"sqlite:///{tmp_path / 'migrate.db'}"
        return
    reset_postgres(POSTGRES_URL)
    yield POSTGRES_URL
    reset_postgres(POSTGRES_URL)


@pytest.fixture()
def migrated_app(database_url):
    """An app on an untouched database. Tests decide what is in it before upgrading."""
    application = create_app(
        {"TESTING": True, "SECRET_KEY": "test-only-secret", "SQLALCHEMY_DATABASE_URI": database_url}
    )
    with application.app_context():
        yield application
        db.session.remove()
        db.engine.dispose()


def schema_differences():
    with db.engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        return compare_metadata(context, db.metadata)


def table_names():
    return set(sa.inspect(db.engine).get_table_names())


def test_migrations_have_one_head():
    script = ScriptDirectory(str(MIGRATIONS_DIRECTORY))

    assert len(script.get_heads()) == 1


def test_upgrade_on_an_empty_database_matches_the_models(migrated_app):
    upgrade()

    assert schema_differences() == []
    assert db.session.scalar(db.select(db.func.count(Employee.id))) == 0


def test_upgrade_is_repeatable(migrated_app):
    upgrade()
    upgrade()

    assert schema_differences() == []


def test_first_release_database_upgrades_and_keeps_its_rows(migrated_app):
    old = legacy_schema.first_release()
    old.create_all(db.engine)
    with db.engine.begin() as connection:
        connection.execute(
            old.tables["employee"].insert(),
            {"name": "Legacy Example", "position": "Clerk", "salary": Decimal("50000")},
        )

    upgrade()

    legacy = db.session.scalar(db.select(Employee))
    assert legacy.name == "Legacy Example"
    assert legacy.salary == Decimal("50000.00")
    assert legacy.status == "active"
    assert legacy.department is None and legacy.manager is None
    assert schema_differences() == []


def test_database_from_before_migrations_upgrades_and_keeps_its_rows(migrated_app):
    old = legacy_schema.before_migrations()
    old.create_all(db.engine)
    with db.engine.begin() as connection:
        connection.execute(old.tables["department"].insert(), {"name": "Finance"})
        connection.execute(
            old.tables["employee"].insert(),
            [
                {"name": "Harper Example", "position": "Finance Manager", "salary": Decimal("134000"),
                 "department_id": 1, "manager_id": None, "status": "active", "start_date": date(2020, 3, 2)},
                {"name": "Rowan Example", "position": "Accountant", "salary": Decimal("86000"),
                 "department_id": 1, "manager_id": 1, "status": "on_leave", "start_date": None},
            ],
        )
        connection.execute(
            old.tables["compensation_change"].insert(),
            {"employee_id": 2, "position": "Accountant", "salary": Decimal("86000"),
             "effective_date": date(2024, 1, 8), "reason": "Hired", "recorded_at": datetime(2024, 1, 8, 9, 0)},
        )
        connection.execute(
            old.tables["time_off_request"].insert(),
            {"employee_id": 2, "kind": "vacation", "start_date": date(2026, 7, 6), "end_date": date(2026, 7, 10),
             "note": "", "status": "approved", "requested_at": datetime(2026, 6, 1, 9, 0), "decided_by_id": 1,
             "decided_at": datetime(2026, 6, 2, 9, 0)},
        )

    upgrade()

    rowan = db.session.get(Employee, 2)
    assert rowan.manager.name == "Harper Example"
    assert rowan.department.name == "Finance"
    assert rowan.status == "on_leave"
    assert [change.reason for change in rowan.compensation_history] == ["Hired"]
    assert rowan.time_off_requests[0].decided_by.name == "Harper Example"
    assert schema_differences() == []


def test_downgrade_to_base_removes_every_table(migrated_app):
    upgrade()

    downgrade(revision="base")

    assert table_names() <= {"alembic_version"}


def test_rows_survive_the_employee_table_rebuild(migrated_app):
    """Revision 0004 adds a column in batch mode, which on SQLite copies the
    whole employee table. Nothing may be lost on the way."""
    upgrade(revision="0003")
    with db.engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO department (name) VALUES ('Finance')"))
        connection.execute(
            sa.text(
                "INSERT INTO employee (name, position, salary, department_id, manager_id, status, start_date) VALUES "
                "('Harper Example', 'Finance Manager', 134000, 1, NULL, 'active', '2020-03-02'), "
                "('Rowan Example', 'Accountant', 86000.50, 1, 1, 'terminated', NULL)"
            )
        )

    upgrade()

    harper, rowan = db.session.scalars(db.select(Employee).order_by(Employee.id)).all()
    assert (harper.name, harper.start_date, harper.status) == ("Harper Example", date(2020, 3, 2), "active")
    assert (rowan.salary, rowan.manager_id, rowan.department.name) == (Decimal("86000.50"), 1, "Finance")
    assert rowan.end_date is None and rowan.checklist_tasks == []
    assert schema_differences() == []


def test_every_revision_downgrades_and_upgrades_again(migrated_app):
    upgrade()
    revisions = [script.revision for script in ScriptDirectory(str(MIGRATIONS_DIRECTORY)).walk_revisions()]

    for _ in revisions[:-1]:
        downgrade()
    assert table_names() == {"alembic_version", "department", "employee", "compensation_change", "time_off_request"}
    upgrade()

    assert schema_differences() == []
