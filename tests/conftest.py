import re

import pytest

from app import Account, Department, Employee, create_app, db, hash_password

TEST_PASSWORD = "correct horse battery staple"
HR_EMAIL = "hr@peoplebase.test"
# The cheapest settings Argon2 accepts. Real deployments use the library defaults.
FAST_HASHING = {"time_cost": 1, "memory_cost": 8, "parallelism": 1}
CSRF_FIELD = re.compile(rb'name="csrf_token" value="([^"]+)"')


@pytest.fixture()
def app(tmp_path):
    application = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-only-secret",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
            "PASSWORD_HASH_OPTIONS": FAST_HASHING,
        }
    )

    with application.app_context():
        # Tests build the schema straight from the models because it is fast.
        # tests/test_migrations.py proves the migrations produce the same schema.
        db.create_all()
        db.session.add(Employee(name="Avery Example", position="Engineer", salary=125000))
        # An HR admin with no employee record, so it never changes a headcount.
        db.session.add(
            Account(
                email=HR_EMAIL, role="admin", password_hash=hash_password(TEST_PASSWORD), must_change_password=False
            )
        )
        db.session.commit()

    yield application

    with application.app_context():
        db.drop_all()
        db.engine.dispose()


def csrf_from(client, path="/login"):
    """Read the CSRF token the way a browser would: from a rendered form."""
    response = client.get(path, follow_redirects=True)
    match = CSRF_FIELD.search(response.data)
    assert match, f"{path} should render a CSRF token"
    return match.group(1).decode()


def sign_in_client(app, email, password=TEST_PASSWORD):
    client = app.test_client()
    response = client.post("/login", data={"email": email, "password": password, "csrf_token": csrf_from(client)})
    assert response.status_code == 302, f"sign-in as {email} failed"
    return client


@pytest.fixture()
def anonymous(app):
    """A browser with no session."""
    return app.test_client()


@pytest.fixture()
def client(app):
    """Signed in as the HR admin, who may do everything."""
    return sign_in_client(app, HR_EMAIL)


@pytest.fixture()
def csrf_token(client):
    return csrf_from(client, "/employees/new")


@pytest.fixture()
def sign_in(app):
    """sign_in("riley@peoplebase.test") returns a client signed in as that account."""

    def _sign_in(email, password=TEST_PASSWORD):
        return sign_in_client(app, email, password)

    return _sign_in


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


@pytest.fixture()
def staff(app):
    """A small company with one account per person.

        Morgan (manager)
        |-- Riley (manager)
        |   |-- Casey (employee)
        |   `-- Jordan (employee)
        |-- Harper (manager)
        |   `-- Rowan (employee)
        `-- Emerson (HR admin)

    Returns name -> employee id. Each person signs in as <name>@peoplebase.test.
    """
    people = [
        # key, title, manager, role, salary
        ("morgan", "Chief Operating Officer", None, "manager", 210000),
        ("riley", "Engineering Director", "morgan", "manager", 185000),
        ("casey", "Engineer", "riley", "employee", 128000),
        ("jordan", "Senior Engineer", "riley", "employee", 152000),
        ("harper", "Finance Manager", "morgan", "manager", 134000),
        ("rowan", "Accountant", "harper", "employee", 86000),
        ("emerson", "People Partner", "morgan", "admin", 104000),
    ]
    with app.app_context():
        engineering = Department(name="Engineering")
        db.session.add(engineering)
        password_hash = hash_password(TEST_PASSWORD)
        records = {}
        for key, title, manager, role, salary in people:
            employee = Employee(
                name=f"{key.title()} Example",
                position=title,
                salary=salary,
                manager=records.get(manager),
                department=engineering if key in {"riley", "casey", "jordan"} else None,
            )
            records[key] = employee
            db.session.add(employee)
            db.session.add(
                Account(
                    email=f"{key}@peoplebase.test",
                    role=role,
                    employee=employee,
                    password_hash=password_hash,
                    must_change_password=False,
                )
            )
        db.session.commit()
        return {key: employee.id for key, employee in records.items()}
