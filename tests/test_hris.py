import csv
import io
from datetime import date
from decimal import Decimal

import pytest

from app import CompensationChange, Department, Employee, db


@pytest.fixture()
def org(app):
    """Two departments and a three-level reporting line: Morgan > Riley > Sam."""
    with app.app_context():
        engineering = Department(name="Engineering")
        design = Department(name="Design")
        morgan = Employee(name="Morgan Example", position="Director", salary=200000, department=engineering)
        riley = Employee(name="Riley Example", position="Lead", salary=150000, department=engineering, manager=morgan)
        sam = Employee(name="Sam Example", position="Designer", salary=95000, department=design, manager=riley)
        db.session.add_all([engineering, design, morgan, riley, sam])
        db.session.commit()
        return {
            "engineering": engineering.id,
            "design": design.id,
            "morgan": morgan.id,
            "riley": riley.id,
            "sam": sam.id,
        }


def employee_form(csrf_token, **overrides):
    data = {
        "name": "Jordan Sample",
        "position": "Analyst",
        "salary": "80000",
        "status": "active",
        "start_date": "2024-03-04",
        "department_id": "",
        "manager_id": "",
        "csrf_token": csrf_token,
    }
    data.update(overrides)
    return data


def count(app, model):
    with app.app_context():
        return db.session.scalar(db.select(db.func.count()).select_from(model))


# Employment details


def test_create_records_department_manager_status_and_start(client, app, csrf_token, org):
    response = client.post(
        "/employees/new",
        data=employee_form(
            csrf_token,
            department_id=str(org["engineering"]),
            manager_id=str(org["riley"]),
            status="on_leave",
        ),
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Added Jordan Sample" in response.data
    assert b"Compensation history" in response.data
    with app.app_context():
        employee = db.session.scalar(db.select(Employee).where(Employee.name == "Jordan Sample"))
        assert employee.department.name == "Engineering"
        assert employee.manager.name == "Riley Example"
        assert employee.status == "on_leave"
        assert employee.start_date == date(2024, 3, 4)
        [hire] = employee.compensation_history
        assert (hire.position, hire.salary, hire.effective_date, hire.reason) == (
            "Analyst",
            Decimal("80000.00"),
            date(2024, 3, 4),
            "Hired",
        )


def test_employment_fields_are_optional(client, app, csrf_token):
    client.post(
        "/employees/new",
        data={"name": "Jordan Sample", "position": "Analyst", "salary": "1", "csrf_token": csrf_token},
    )

    with app.app_context():
        employee = db.session.scalar(db.select(Employee).where(Employee.name == "Jordan Sample"))
        assert employee.status == "active"
        assert employee.department is None
        assert employee.manager is None
        assert employee.start_date is None
        assert employee.compensation_history[0].effective_date == date.today()


@pytest.mark.parametrize(
    "overrides,error",
    [
        ({"status": "retired"}, b"valid employment status"),
        ({"start_date": "04/03/2024"}, b"YYYY-MM-DD"),
        ({"start_date": "2024-02-30"}, b"real calendar date"),
        ({"start_date": "1800-01-01"}, b"between 1900 and 2100"),
        ({"department_id": "999"}, b"valid department"),
        ({"department_id": "abc"}, b"valid department"),
        ({"department_id": "99999999999999999999"}, b"valid department"),
        ({"manager_id": "999"}, b"valid manager"),
        ({"manager_id": "-1"}, b"valid manager"),
    ],
)
def test_invalid_employment_details_are_rejected(client, app, csrf_token, overrides, error):
    response = client.post("/employees/new", data=employee_form(csrf_token, **overrides))

    assert response.status_code == 400
    assert error in response.data
    assert count(app, Employee) == 1


def test_terminated_people_cannot_be_managers(client, app, csrf_token, org):
    with app.app_context():
        db.session.get(Employee, org["riley"]).status = "terminated"
        db.session.commit()

    response = client.post(
        "/employees/new", data=employee_form(csrf_token, manager_id=str(org["riley"]))
    )

    assert response.status_code == 400
    assert b"still works here" in response.data


def test_employee_cannot_manage_themselves(client, app, csrf_token, org):
    response = client.post(
        f"/employees/{org['riley']}/edit",
        data=employee_form(csrf_token, name="Riley Example", manager_id=str(org["riley"])),
    )

    assert response.status_code == 400
    assert b"their own manager" in response.data


def test_reporting_lines_cannot_loop(client, app, csrf_token, org):
    # Sam reports to Riley, who reports to Morgan. Morgan cannot report to Sam.
    response = client.post(
        f"/employees/{org['morgan']}/edit",
        data=employee_form(
            csrf_token, name="Morgan Example", position="Director", salary="200000", manager_id=str(org["sam"])
        ),
    )

    assert response.status_code == 400
    assert b"already reports to Morgan Example" in response.data
    with app.app_context():
        assert db.session.get(Employee, org["morgan"]).manager is None


def test_profile_shows_manager_reports_and_history(client, org):
    response = client.get(f"/employees/{org['riley']}")

    assert response.status_code == 200
    assert b"Riley Example" in response.data
    assert b"Morgan Example" in response.data
    assert b"Sam Example" in response.data
    assert b"Engineering" in response.data


def test_profile_of_missing_employee_is_404(client):
    assert client.get("/employees/999").status_code == 404


def test_deleting_a_manager_keeps_their_reports(client, app, csrf_token, org):
    client.post(f"/employees/{org['riley']}/delete", data={"csrf_token": csrf_token})

    with app.app_context():
        sam = db.session.get(Employee, org["sam"])
        assert sam is not None
        assert sam.manager_id is None


# Compensation history


def test_salary_change_is_recorded_with_effective_date(client, app, csrf_token, org):
    response = client.post(
        f"/employees/{org['sam']}/edit",
        data=employee_form(
            csrf_token,
            name="Sam Example",
            position="Senior Designer",
            salary="105000",
            department_id=str(org["design"]),
            manager_id=str(org["riley"]),
            effective_date="2026-01-01",
            reason="Promotion",
        ),
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Promotion" in response.data
    with app.app_context():
        sam = db.session.get(Employee, org["sam"])
        assert sam.position == "Senior Designer"
        assert sam.salary == Decimal("105000.00")
        [change] = sam.compensation_history
        assert change.effective_date == date(2026, 1, 1)
        assert change.reason == "Promotion"
        assert change.salary == Decimal("105000.00")


def test_edit_without_title_or_salary_change_adds_no_history(client, app, csrf_token, org):
    client.post(
        f"/employees/{org['sam']}/edit",
        data=employee_form(
            csrf_token, name="Sam Example", position="Designer", salary="95000", status="on_leave"
        ),
    )

    with app.app_context():
        sam = db.session.get(Employee, org["sam"])
        assert sam.status == "on_leave"
        assert sam.compensation_history == []


def test_invalid_effective_date_rejects_the_whole_edit(client, app, csrf_token, org):
    response = client.post(
        f"/employees/{org['sam']}/edit",
        data=employee_form(
            csrf_token, name="Sam Example", position="Designer", salary="1", effective_date="soon"
        ),
    )

    assert response.status_code == 400
    with app.app_context():
        assert db.session.get(Employee, org["sam"]).salary == Decimal("95000.00")
        assert count(app, CompensationChange) == 0


def test_history_is_newest_first(client, app, csrf_token, org):
    for salary, effective in [("96000", "2025-01-01"), ("97000", "2026-01-01")]:
        client.post(
            f"/employees/{org['sam']}/edit",
            data=employee_form(
                csrf_token, name="Sam Example", position="Designer", salary=salary, effective_date=effective
            ),
        )

    with app.app_context():
        history = db.session.get(Employee, org["sam"]).compensation_history
        assert [change.effective_date.year for change in history] == [2026, 2025]


def test_deleting_an_employee_deletes_their_history(client, app, csrf_token):
    client.post(
        "/employees/new",
        data={"name": "Jordan Sample", "position": "Analyst", "salary": "1", "csrf_token": csrf_token},
    )
    with app.app_context():
        employee_id = db.session.scalar(db.select(Employee.id).where(Employee.name == "Jordan Sample"))

    client.post(f"/employees/{employee_id}/delete", data={"csrf_token": csrf_token})

    assert count(app, CompensationChange) == 0


# Departments


def test_department_list_shows_headcount(client, org):
    response = client.get("/departments")

    assert response.status_code == 200
    assert b"Engineering" in response.data
    assert b"2 people" in response.data
    assert b"1 person" in response.data


def test_department_can_be_added(client, app, csrf_token):
    response = client.post(
        "/departments", data={"name": "  Finance ", "csrf_token": csrf_token}, follow_redirects=True
    )

    assert b"Added the Finance department" in response.data
    with app.app_context():
        assert db.session.scalar(db.select(Department.name)) == "Finance"


@pytest.mark.parametrize(
    "name,error",
    [
        ("", b"Department name is required"),
        ("engineering", b"already exists"),
        ("A" * 101, b"100 characters"),
        ("Ops\nTeam", b"control characters"),
    ],
)
def test_invalid_department_names_are_rejected(client, app, csrf_token, org, name, error):
    response = client.post("/departments", data={"name": name, "csrf_token": csrf_token})

    assert response.status_code == 400
    assert error in response.data
    assert count(app, Department) == 2


def test_department_actions_require_csrf(client, app, org):
    assert client.post("/departments", data={"name": "Finance"}).status_code == 400
    assert client.post(f"/departments/{org['design']}/delete").status_code == 400
    assert count(app, Department) == 2


def test_empty_department_can_be_deleted(client, app, csrf_token):
    with app.app_context():
        db.session.add(Department(name="Finance"))
        db.session.commit()

    response = client.post("/departments/1/delete", data={"csrf_token": csrf_token}, follow_redirects=True)

    assert b"Deleted the Finance department" in response.data
    assert count(app, Department) == 0


def test_department_with_people_is_not_deleted(client, app, csrf_token, org):
    response = client.post(
        f"/departments/{org['design']}/delete", data={"csrf_token": csrf_token}, follow_redirects=True
    )

    assert b"Design still has 1 person. Move them to another department first." in response.data
    assert count(app, Department) == 2



def department_name(app, department_id):
    with app.app_context():
        return db.session.get(Department, department_id).name


def test_department_can_be_renamed(client, app, csrf_token, org):
    response = client.post(
        f"/departments/{org['design']}/rename",
        data={"name": "  Product Design ", "csrf_token": csrf_token},
        follow_redirects=True,
    )

    assert b"Renamed Design to Product Design." in response.data
    assert department_name(app, org["design"]) == "Product Design"
    with app.app_context():
        assert db.session.get(Employee, org["sam"]).department_id == org["design"]


def test_department_rename_can_change_only_capitalization(client, app, csrf_token, org):
    response = client.post(
        f"/departments/{org['design']}/rename",
        data={"name": "DESIGN", "csrf_token": csrf_token},
        follow_redirects=True,
    )

    assert b"Renamed Design to DESIGN." in response.data
    assert department_name(app, org["design"]) == "DESIGN"


def test_department_rename_to_same_name_changes_nothing(client, app, csrf_token, org):
    response = client.post(
        f"/departments/{org['design']}/rename",
        data={"name": "Design", "csrf_token": csrf_token},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Design already has that name." in response.data
    assert department_name(app, org["design"]) == "Design"


@pytest.mark.parametrize(
    "name,error",
    [
        ("", b"Department name is required"),
        ("   ", b"Department name is required"),
        ("engineering", b"A department named Engineering already exists"),
        ("A" * 101, b"100 characters"),
        ("Ops\tTeam", b"control characters"),
        ("Ops\x00", b"control characters"),
    ],
)
def test_invalid_department_renames_are_rejected(client, app, csrf_token, org, name, error):
    response = client.post(
        f"/departments/{org['design']}/rename", data={"name": name, "csrf_token": csrf_token}
    )

    assert response.status_code == 400
    assert error in response.data
    # The form stays open with what was typed so it can be corrected.
    assert b'<details class="rename" open>' in response.data
    assert department_name(app, org["design"]) == "Design"


def test_department_rename_accepts_the_length_limit(client, app, csrf_token, org):
    client.post(f"/departments/{org['design']}/rename", data={"name": "A" * 100, "csrf_token": csrf_token})

    assert department_name(app, org["design"]) == "A" * 100


def test_department_rename_requires_csrf(client, app, org):
    response = client.post(f"/departments/{org['design']}/rename", data={"name": "Studio"})

    assert response.status_code == 400
    assert department_name(app, org["design"]) == "Design"


def test_department_rename_is_post_only(client, org):
    assert client.get(f"/departments/{org['design']}/rename").status_code == 405


def test_renaming_a_missing_department_is_not_found(client, csrf_token):
    response = client.post("/departments/999/rename", data={"name": "Studio", "csrf_token": csrf_token})

    assert response.status_code == 404


def test_department_list_offers_rename(client, org):
    response = client.get("/departments")

    assert f'action="/departments/{org["design"]}/rename"'.encode() in response.data
    assert b'<details class="rename" open>' not in response.data


# Directory filters


def test_directory_filters_by_department_and_status(client, app, org):
    with app.app_context():
        db.session.get(Employee, org["riley"]).status = "on_leave"
        db.session.commit()

    by_department = client.get(f"/employees?department={org['engineering']}")
    by_status = client.get("/employees?status=on_leave")

    assert b"Morgan Example" in by_department.data
    assert b"Sam Example" not in by_department.data
    assert b"Riley Example" in by_status.data
    assert b"Morgan Example" not in by_status.data


@pytest.mark.parametrize("query", ["department=abc", "department=99999999999999999999", "status=retired"])
def test_invalid_directory_filters_are_ignored(client, org, query):
    response = client.get(f"/employees?{query}")

    assert response.status_code == 200
    assert b"Sam Example" in response.data


def test_csv_export_honours_department_filter(client, org):
    response = client.get(f"/employees.csv?department={org['design']}")
    rows = list(csv.reader(io.StringIO(response.get_data(as_text=True))))

    assert [row[1] for row in rows[1:]] == ["Sam Example"]


def test_directory_export_link_keeps_new_filters(client, org):
    response = client.get(f"/employees?department={org['design']}&status=active")

    assert f"/employees.csv?department={org['design']}&amp;status=active".encode() in response.data


def test_edit_keeps_fields_the_form_leaves_out(client, app, csrf_token, org):
    client.post(
        f"/employees/{org['sam']}/edit",
        data={"name": "Sam Example", "position": "Designer", "salary": "95000", "csrf_token": csrf_token},
    )

    with app.app_context():
        sam = db.session.get(Employee, org["sam"])
        assert sam.manager_id == org["riley"]
        assert sam.department_id == org["design"]


def test_edit_with_blank_fields_clears_them(client, app, csrf_token, org):
    client.post(
        f"/employees/{org['sam']}/edit",
        data=employee_form(csrf_token, name="Sam Example", position="Designer", salary="95000", start_date=""),
    )

    with app.app_context():
        sam = db.session.get(Employee, org["sam"])
        assert sam.manager_id is None
        assert sam.department_id is None
        assert sam.start_date is None
