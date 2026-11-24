"""Job history: who holds a title, department and manager over time, and who
may read or write it. Abuse case 19 in docs/security.md."""

from datetime import date

import pytest

from app import Department, Employee, JobChange, db
from tests.conftest import csrf_from


def post(client, path, **data):
    return client.post(path, data={"csrf_token": csrf_from(client, "/"), **data})


def history(app, employee_id):
    with app.app_context():
        rows = db.session.scalars(
            db.select(JobChange).where(JobChange.employee_id == employee_id).order_by(JobChange.id)
        ).all()
        return [(r.position, r.department_name, r.manager_name, r.effective_date, r.reason) for r in rows]


def edit_form(app, person, **changes):
    with app.app_context():
        employee = db.session.get(Employee, person)
        form = {
            "name": employee.name,
            "position": employee.position,
            "salary": str(employee.salary),
            "department_id": employee.department_id or "",
            "manager_id": employee.manager_id or "",
            "status": employee.status,
        }
    return {**form, **changes}


def test_adding_an_employee_starts_their_job_history(app, client):
    post(client, "/employees/new", name="Sky Example", position="Designer", salary="90000", start_date="2024-05-06")

    with app.app_context():
        new_id = db.session.scalar(db.select(Employee.id).where(Employee.name == "Sky Example"))
    assert history(app, new_id) == [("Designer", "", "", date(2024, 5, 6), "Hired")]


def test_a_title_department_or_manager_change_adds_a_row(app, client, staff):
    with app.app_context():
        finance = Department(name="Finance")
        db.session.add(finance)
        db.session.commit()
        finance_id = finance.id
    casey = staff["casey"]

    post(
        client,
        f"/employees/{casey}/edit",
        **edit_form(app, casey, position="Staff Engineer", department_id=finance_id, manager_id=staff["morgan"]),
        effective_date="2026-03-01",
        reason="Promotion and transfer",
    )

    assert history(app, casey) == [
        ("Staff Engineer", "Finance", "Morgan Example", date(2026, 3, 1), "Promotion and transfer")
    ]


def test_a_pay_only_change_does_not_add_a_job_row(app, client, staff):
    casey = staff["casey"]

    post(client, f"/employees/{casey}/edit", **edit_form(app, casey, salary="140000"), effective_date="2026-03-01")

    assert history(app, casey) == []


def test_a_bad_effective_date_is_refused_and_nothing_changes(app, client, staff):
    casey = staff["casey"]

    response = post(
        client, f"/employees/{casey}/edit", **edit_form(app, casey, position="Lead"), effective_date="not-a-date"
    )

    assert response.status_code == 400
    assert history(app, casey) == []
    with app.app_context():
        assert db.session.get(Employee, casey).position == "Engineer"


def test_history_is_shown_to_those_who_can_open_the_profile(sign_in, staff):
    for who in ("casey", "riley"):
        page = sign_in(f"{who}@peoplebase.test").get(f"/employees/{staff['casey']}")
        assert page.status_code == 200
        assert b"Job history" in page.data


@pytest.mark.parametrize("who", ["casey", "riley", "harper"])
def test_nobody_but_hr_can_write_job_history(app, sign_in, staff, who):
    """Abuse case 19: forge a promotion by posting job fields as yourself, as
    your manager or as an unrelated manager."""
    casey = staff["casey"]
    client = sign_in(f"{who}@peoplebase.test")

    response = post(
        client,
        f"/employees/{casey}/edit",
        name="Casey Example",
        position="Chief Executive",
        salary="999999",
        manager_id="",
        effective_date="2026-01-01",
        reason="Self promotion",
    )

    assert response.status_code == 403
    assert history(app, casey) == []
    with app.app_context():
        assert db.session.get(Employee, casey).position == "Engineer"


def test_a_manager_outside_the_line_cannot_read_another_teams_history(sign_in, staff):
    assert sign_in("harper@peoplebase.test").get(f"/employees/{staff['casey']}").status_code == 404
    assert sign_in("casey@peoplebase.test").get(f"/employees/{staff['jordan']}").status_code == 404


def test_there_is_no_route_to_edit_or_delete_a_history_row(app, client, staff):
    with app.app_context():
        rules = {rule.rule for rule in app.url_map.iter_rules()}
    # HR only ever appends, through the employee edit form.
    assert not any("job" in rule for rule in rules)
