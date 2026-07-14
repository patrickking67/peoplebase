import pytest

from app import Employee, db


def test_home_reports_employee_count(client):
    response = client.get("/")

    assert response.status_code == 200
    assert b"Current records" in response.data
    assert b">1<" in response.data


def test_directory_lists_employees(client):
    response = client.get("/employees")

    assert response.status_code == 200
    assert b"Ada Lovelace" in response.data
    assert b"Engineer" in response.data


def test_directory_searches_names_and_positions(client):
    name_response = client.get("/employees?q=ada")
    missing_response = client.get("/employees?q=designer")

    assert b"Ada Lovelace" in name_response.data
    assert b"No records found" in missing_response.data


def test_directory_filters_by_position(client):
    response = client.get("/employees?position=Engineer")

    assert response.status_code == 200
    assert b"Ada Lovelace" in response.data
    assert b"Annual payroll" in response.data


def test_csv_export_uses_current_filters(client):
    response = client.get("/employees.csv?q=ada")

    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert "peoplebase-employees.csv" in response.headers["Content-Disposition"]
    assert b"id,name,position,annual_salary" in response.data
    assert b"Ada Lovelace,Engineer" in response.data


def test_employee_crud_flow(client, app):
    create_response = client.post(
        "/employees/new",
        data={"name": "Grace Hopper", "position": "Computer Scientist", "salary": "140000"},
        follow_redirects=True,
    )
    assert create_response.status_code == 200
    assert b"Added Grace Hopper" in create_response.data

    with app.app_context():
        employee = db.session.scalar(db.select(Employee).where(Employee.name == "Grace Hopper"))
        employee_id = employee.id

    update_response = client.post(
        f"/employees/{employee_id}/edit",
        data={"name": "Grace Hopper", "position": "Rear Admiral", "salary": "150000.50"},
        follow_redirects=True,
    )
    assert update_response.status_code == 200
    assert b"Rear Admiral" in update_response.data

    delete_response = client.post(
        f"/employees/{employee_id}/delete", follow_redirects=True
    )
    assert delete_response.status_code == 200
    assert b"Deleted Grace Hopper" in delete_response.data

    with app.app_context():
        assert db.session.get(Employee, employee_id) is None


@pytest.mark.parametrize(
    "data,error",
    [
        ({"name": "", "position": "Engineer", "salary": "1"}, b"required"),
        ({"name": "A", "position": "Engineer", "salary": "nope"}, b"must be a number"),
        ({"name": "A", "position": "Engineer", "salary": "-1"}, b"must be between"),
    ],
)
def test_invalid_employee_data_is_rejected(client, data, error):
    response = client.post("/employees/new", data=data)

    assert response.status_code == 400
    assert error in response.data


def test_delete_rejects_get_requests(client):
    response = client.get("/employees/1/delete")

    assert response.status_code == 405
