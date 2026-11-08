from decimal import Decimal

import pytest

from app import Employee, db


def test_home_reports_employee_count(client):
    response = client.get("/")

    assert response.status_code == 200
    assert b"Current records" in response.data
    assert b">1<" in response.data


def test_employee_crud_flow(client, app, csrf_token):
    create_response = client.post(
        "/employees/new",
        data={
            "name": "Jordan Sample",
            "position": "Data Analyst",
            "salary": "140000",
            "csrf_token": csrf_token,
        },
        follow_redirects=True,
    )
    assert create_response.status_code == 200
    assert b"Added Jordan Sample" in create_response.data

    with app.app_context():
        employee = db.session.scalar(db.select(Employee).where(Employee.name == "Jordan Sample"))
        employee_id = employee.id

    update_response = client.post(
        f"/employees/{employee_id}/edit",
        data={
            "name": "Jordan Sample",
            "position": "Analytics Lead",
            "salary": "150000.50",
            "csrf_token": csrf_token,
        },
        follow_redirects=True,
    )
    assert update_response.status_code == 200
    assert b"Analytics Lead" in update_response.data

    with app.app_context():
        assert db.session.get(Employee, employee_id).salary == Decimal("150000.50")

    delete_response = client.post(
        f"/employees/{employee_id}/delete",
        data={"csrf_token": csrf_token},
        follow_redirects=True,
    )
    assert delete_response.status_code == 200
    assert b"Deleted Jordan Sample" in delete_response.data

    with app.app_context():
        assert db.session.get(Employee, employee_id) is None


@pytest.mark.parametrize(
    "data,error",
    [
        ({"name": "", "position": "Engineer", "salary": "1"}, b"required"),
        ({"name": "   ", "position": "Engineer", "salary": "1"}, b"required"),
        ({"name": "A" * 101, "position": "Engineer", "salary": "1"}, b"100 characters"),
        ({"name": "A", "position": "Engineer", "salary": "nope"}, b"must be a number"),
        ({"name": "A", "position": "Engineer", "salary": "125,000"}, b"must be a number"),
        ({"name": "A", "position": "Engineer", "salary": "nan"}, b"must be a number"),
        ({"name": "A", "position": "Engineer", "salary": "NaN"}, b"must be a number"),
        ({"name": "A", "position": "Engineer", "salary": "inf"}, b"must be a number"),
        ({"name": "A", "position": "Engineer", "salary": "-Infinity"}, b"must be a number"),
        ({"name": "A", "position": "Engineer", "salary": "-1"}, b"must be between"),
        ({"name": "A", "position": "Engineer", "salary": "1e999"}, b"must be between"),
        ({"name": "A", "position": "Engineer", "salary": "1000000000"}, b"must be between"),
        ({"name": "A", "position": "Engineer", "salary": "1_000"}, b"must be a number"),
        ({"name": "A", "position": "Engineer", "salary": "\u0661\u0662\u0663"}, b"must be a number"),
        ({"name": "A", "position": "Engineer", "salary": "0x10"}, b"must be a number"),
        ({"name": "A\x00B", "position": "Engineer", "salary": "1"}, b"control characters"),
        ({"name": "A", "position": "Line\nBreak", "salary": "1"}, b"control characters"),
        ({"name": "A", "position": "Tab\tbed", "salary": "1"}, b"control characters"),
    ],
)
def test_invalid_employee_data_is_rejected(client, app, csrf_token, data, error):
    response = client.post("/employees/new", data={**data, "csrf_token": csrf_token})

    assert response.status_code == 400
    assert error in response.data
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Employee.id))) == 1


def test_salary_is_rounded_to_cents(client, app, csrf_token):
    client.post(
        "/employees/new",
        data={"name": "Casey Placeholder", "position": "Engineer", "salary": "10.005", "csrf_token": csrf_token},
    )

    with app.app_context():
        employee = db.session.scalar(db.select(Employee).where(Employee.name == "Casey Placeholder"))
        assert employee.salary == Decimal("10.01")


def test_invalid_update_keeps_existing_record(client, app, csrf_token):
    response = client.post(
        "/employees/1/edit",
        data={"name": "Avery Example", "position": "Engineer", "salary": "nan", "csrf_token": csrf_token},
    )

    assert response.status_code == 400
    with app.app_context():
        assert db.session.get(Employee, 1).salary == Decimal("125000.00")


def test_missing_employee_returns_404(client, csrf_token):
    assert client.get("/employees/999/edit").status_code == 404
    assert client.post("/employees/999/delete", data={"csrf_token": csrf_token}).status_code == 404


def test_delete_rejects_get_requests(client):
    response = client.get("/employees/1/delete")

    assert response.status_code == 405


def test_user_input_is_html_escaped(client, add_employees):
    add_employees(("<script>alert(1)</script>", "Engineer", 1))

    response = client.get("/employees")

    assert b"<script>alert(1)</script>" not in response.data
    assert b"&lt;script&gt;alert(1)&lt;/script&gt;" in response.data


@pytest.mark.parametrize(
    "method,path,status,text",
    [
        ("get", "/does-not-exist", 404, b"Not Found"),
        ("get", "/employees/999/edit", 404, b"Not Found"),
        ("get", "/employees/1/delete", 405, b"Method Not Allowed"),
        ("post", "/employees/new", 400, b"did not come from PeopleBase"),
    ],
)
def test_error_pages_use_the_app_layout(client, method, path, status, text):
    response = getattr(client, method)(path)

    assert response.status_code == status
    assert text in response.data
    assert b"Back to PeopleBase" in response.data
    assert b'class="site-header"' in response.data


def test_unexpected_errors_show_a_generic_page(app):
    app.config["PROPAGATE_EXCEPTIONS"] = False

    @app.get("/boom")
    def boom():
        raise RuntimeError("secret internal detail")

    response = app.test_client().get("/boom")

    assert response.status_code == 500
    assert b"Internal Server Error" in response.data
    assert b"secret internal detail" not in response.data


def test_layout_links_brand_assets_and_marks_current_page(client):
    response = client.get("/employees")

    assert b'href="/static/favicon.svg"' in response.data
    assert b'href="/static/apple-touch-icon.png"' in response.data
    assert b'<a href="/employees" aria-current="page">Directory</a>' in response.data
    assert b'href="#main"' in response.data


@pytest.mark.parametrize(
    "path", ["/static/favicon.svg", "/static/favicon.ico", "/static/apple-touch-icon.png", "/static/brand/logo.svg"]
)
def test_brand_assets_are_served(client, path):
    assert client.get(path).status_code == 200
