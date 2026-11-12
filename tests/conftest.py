import re

import pytest

from app import Employee, create_app, db


@pytest.fixture()
def app(tmp_path):
    application = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-only-secret",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
        }
    )

    with application.app_context():
        # Tests build the schema straight from the models because it is fast.
        # tests/test_migrations.py proves the migrations produce the same schema.
        db.create_all()
        db.session.add(Employee(name="Avery Example", position="Engineer", salary=125000))
        db.session.commit()

    yield application

    with application.app_context():
        db.drop_all()
        db.engine.dispose()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def csrf_token(client):
    """Read the CSRF token the way a browser would: from a rendered form."""
    response = client.get("/employees/new")
    match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
    assert match, "add form should render a CSRF token"
    return match.group(1).decode()


@pytest.fixture()
def add_employees(app):
    def _add(*records):
        with app.app_context():
            db.session.add_all(
                Employee(name=name, position=position, salary=salary)
                for name, position, salary in records
            )
            db.session.commit()

    return _add
