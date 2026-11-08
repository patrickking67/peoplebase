import csv
import io

import pytest

from app import Employee, create_app, db, neutralize_formula

# CSRF


def test_forms_render_a_csrf_token(client):
    assert b'name="csrf_token"' in client.get("/employees/new").data
    assert b'name="csrf_token"' in client.get("/employees/1/edit").data
    assert b'name="csrf_token"' in client.get("/employees").data


@pytest.mark.parametrize("token", [None, "", "forged-token", "tökén"])
def test_create_requires_valid_csrf_token(client, app, token):
    data = {"name": "Jordan Sample", "position": "Engineer", "salary": "1"}
    if token is not None:
        data["csrf_token"] = token
    client.get("/employees/new")  # start a session so a real token exists

    response = client.post("/employees/new", data=data)

    assert response.status_code == 400
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Employee.id))) == 1


def test_update_requires_csrf_token(client, app):
    response = client.post(
        "/employees/1/edit", data={"name": "Changed", "position": "Engineer", "salary": "1"}
    )

    assert response.status_code == 400
    with app.app_context():
        assert db.session.get(Employee, 1).name == "Avery Example"


def test_delete_requires_csrf_token(client, app):
    response = client.post("/employees/1/delete")

    assert response.status_code == 400
    with app.app_context():
        assert db.session.get(Employee, 1) is not None


def test_token_from_another_session_is_rejected(app, csrf_token):
    other_browser = app.test_client()

    response = other_browser.post("/employees/1/delete", data={"csrf_token": csrf_token})

    assert response.status_code == 400


def test_session_cookie_is_samesite_lax(client):
    response = client.get("/employees/new")

    assert "SameSite=Lax" in response.headers["Set-Cookie"]
    assert "HttpOnly" in response.headers["Set-Cookie"]


# Configuration


def test_secret_key_is_required(monkeypatch, tmp_path):
    monkeypatch.delenv("SECRET_KEY", raising=False)

    with pytest.raises(RuntimeError, match="SECRET_KEY is not set"):
        create_app({"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'x.db'}"})


def test_secret_key_is_read_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "from-the-environment")

    application = create_app({"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'x.db'}"})

    assert application.config["SECRET_KEY"] == "from-the-environment"
    assert application.debug is False
    with application.app_context():
        db.engine.dispose()


# CSV injection


@pytest.mark.parametrize(
    "value,expected",
    [
        ("=1+1", "'=1+1"),
        ("+1+1", "'+1+1"),
        ("-2+3", "'-2+3"),
        ("@SUM(A1:A2)", "'@SUM(A1:A2)"),
        ("\t=1+1", "'\t=1+1"),
        ("\r=1+1", "'\r=1+1"),
        ("Avery Example", "Avery Example"),
        ("Senior Engineer - Platform", "Senior Engineer - Platform"),
        ("", ""),
        (125000, 125000),
    ],
)
def test_neutralize_formula(value, expected):
    assert neutralize_formula(value) == expected


def test_csv_export_neutralizes_formula_cells(client, add_employees):
    add_employees(
        ('=HYPERLINK("http://example.com","click")', "Engineer", 1),
        ("@SUM(1+1)", "=cmd|' /C calc'!A0", 2),
        ("Plain Person", "+Analyst", 3),
    )

    response = client.get("/employees.csv")
    rows = list(csv.reader(io.StringIO(response.get_data(as_text=True))))[1:]
    cells = [cell for row in rows for cell in row[1:3]]

    assert not [cell for cell in cells if cell.startswith(("=", "+", "-", "@", "\t", "\r"))]
    assert "'=HYPERLINK(\"http://example.com\",\"click\")" in cells
    assert "'=cmd|' /C calc'!A0" in cells
    assert "'+Analyst" in cells
    assert "Avery Example" in cells


# Response headers and request limits


@pytest.mark.parametrize("path", ["/", "/employees", "/employees/new", "/employees.csv"])
def test_security_headers_are_set(client, path):
    response = client.get(path)

    csp = response.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "same-origin"


def test_templates_have_no_inline_event_handlers(client):
    for path in ["/", "/employees", "/employees/new", "/employees/1/edit"]:
        assert b"onsubmit=" not in client.get(path).data
        assert b"onclick=" not in client.get(path).data


def test_delete_form_asks_for_confirmation(client):
    assert b'data-confirm="Delete Avery Example? This cannot be undone."' in client.get("/employees").data


def test_csv_export_is_not_cached(client):
    assert client.get("/employees.csv").headers["Cache-Control"] == "no-store"


def test_oversized_request_body_is_rejected(client, app, csrf_token):
    response = client.post(
        "/employees/new",
        data={"name": "A" * 70_000, "position": "Engineer", "salary": "1", "csrf_token": csrf_token},
    )

    assert response.status_code == 413
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Employee.id))) == 1
