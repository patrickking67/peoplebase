"""The audit log: what gets recorded, who can read it and that entries cannot
be changed. Abuse case 13 in docs/security.md."""

import json
from datetime import date

import pytest
from sqlalchemy.orm import Session

from app import Account, AuditEvent, Employee, TimeOffRequest, audit, db
from models import AUDIT_ACTIONS, AUDIT_GROUPS, AuditLogError
from tests.conftest import HR_EMAIL, TEST_PASSWORD, csrf_from


def post(client, path, **data):
    return client.post(path, data={"csrf_token": csrf_from(client, "/"), **data})


def entries(app, action=None):
    """Audit rows as plain dicts, oldest first."""
    with app.app_context():
        statement = db.select(AuditEvent).order_by(AuditEvent.id)
        if action:
            statement = statement.where(AuditEvent.action == action)
        return [
            {
                "actor": row.actor_label,
                "actor_id": row.actor_account_id,
                "action": row.action,
                "type": row.subject_type,
                "subject_id": row.subject_id,
                "subject": row.subject_label,
                "details": json.loads(row.details),
                "at": row.created_at,
            }
            for row in db.session.scalars(statement)
        ]


def last(app, action):
    found = entries(app, action)
    assert found, f"no {action} entry was written"
    return found[-1]


EDIT = {"name": "Casey Example", "position": "Engineer", "salary": "128000"}


# What gets recorded


def test_sign_in_and_sign_out_are_recorded(app, client):
    post(client, "/logout")

    signed_in, signed_out = last(app, "auth.signed_in"), last(app, "auth.signed_out")
    assert signed_in["actor"] == signed_out["actor"] == HR_EMAIL
    assert signed_in["type"] == "account"
    assert signed_in["at"] is not None


def test_failed_sign_in_records_the_email_but_never_the_password(app, anonymous):
    anonymous.post(
        "/login",
        data={"email": "Casey@PeopleBase.Test", "password": "hunter2 hunter2", "csrf_token": csrf_from(anonymous)},
    )

    entry = last(app, "auth.sign_in_failed")
    assert entry["actor"] == "not signed in"
    assert entry["subject"] == "casey@peoplebase.test"
    assert "hunter2" not in json.dumps(entries(app), default=str)


def test_adding_an_employee_is_recorded(app, client):
    post(client, "/employees/new", name="Quinn Example", position="Designer", salary="112000", start_date="2026-03-02")

    entry = last(app, "employee.created")
    assert entry["actor"] == HR_EMAIL
    assert (entry["type"], entry["subject"]) == ("employee", "Quinn Example")
    assert entry["subject_id"] == 2
    assert entry["details"]["salary"] == "112000.00"
    assert entry["details"]["start_date"] == "2026-03-02"


def test_record_changes_list_only_the_fields_that_changed(app, client, staff):
    post(
        client, f"/employees/{staff['casey']}/edit",
        **{**EDIT, "name": "Casey Sample", "manager_id": str(staff["harper"]), "status": "on_leave"},
    )

    entry = last(app, "employee.updated")
    assert entry["subject"] == "Casey Sample"
    assert entry["details"] == {
        "name": ["Casey Example", "Casey Sample"],
        "manager": ["Riley Example", "Harper Example"],
        "status": ["active", "on_leave"],
    }
    assert entries(app, "compensation.changed") == []


def test_saving_without_changes_writes_nothing(app, client, staff):
    before = len(entries(app))

    post(
        client, f"/employees/{staff['casey']}/edit",
        **{**EDIT, "manager_id": str(staff["riley"]), "department_id": "1"},
    )

    assert len(entries(app)) == before


def test_pay_changes_get_their_own_entry_with_old_and_new_values(app, client, staff):
    post(
        client, f"/employees/{staff['casey']}/edit",
        **{**EDIT, "position": "Senior Engineer", "salary": "141000", "manager_id": str(staff["riley"]),
           "department_id": "1", "effective_date": "2026-12-01", "reason": "Promotion"},
    )

    entry = last(app, "compensation.changed")
    assert entry["actor"] == HR_EMAIL
    assert entry["subject"] == "Casey Example"
    assert entry["details"] == {
        "position": ["Engineer", "Senior Engineer"],
        "salary": ["128000.00", "141000.00"],
        "effective_date": "2026-12-01",
        "reason": "Promotion",
    }
    assert entries(app, "employee.updated") == []


def test_deleting_an_employee_is_recorded_and_the_entries_outlive_them(app, client, staff):
    post(client, f"/employees/{staff['rowan']}/edit", name="Rowan Example", position="Accountant", salary="90000")
    post(client, f"/employees/{staff['rowan']}/delete")

    deleted = last(app, "employee.deleted")
    assert deleted["subject"] == "Rowan Example"
    assert deleted["details"]["salary"] == "90000.00"
    assert last(app, "compensation.changed")["subject_id"] == staff["rowan"]
    with app.app_context():
        assert db.session.get(Employee, staff["rowan"]) is None


def test_role_and_account_changes_are_recorded(app, client, staff):
    created = post(client, "/accounts", email="avery@peoplebase.test", role="employee", employee_id="1")
    with app.app_context():
        account_id = db.session.scalar(db.select(Account.id).where(Account.email == "avery@peoplebase.test"))
    post(client, f"/accounts/{account_id}/role", role="manager")
    post(client, f"/accounts/{account_id}/role", role="manager")  # no change, no entry
    post(client, f"/accounts/{account_id}/password")
    post(client, f"/accounts/{account_id}/active", active="0")
    post(client, f"/accounts/{account_id}/active", active="1")

    assert created.status_code == 201
    assert last(app, "account.created")["details"] == {"role": "employee", "employee": "Avery Example"}
    role_changes = entries(app, "account.role_changed")
    assert [entry["details"] for entry in role_changes] == [{"role": ["employee", "manager"]}]
    assert role_changes[0]["actor"] == HR_EMAIL and role_changes[0]["subject"] == "avery@peoplebase.test"
    for action in ("account.password_reset", "account.turned_off", "account.turned_on"):
        assert last(app, action)["subject"] == "avery@peoplebase.test"


def test_time_off_requests_and_decisions_are_recorded_with_the_real_actor(app, staff, sign_in):
    post(
        sign_in("casey@peoplebase.test"), "/time-off/new",
        kind="vacation", start_date="2026-12-21", end_date="2026-12-24",
    )
    with app.app_context():
        request_id = db.session.scalar(db.select(TimeOffRequest.id))
    post(sign_in("riley@peoplebase.test"), f"/time-off/{request_id}/decide", decision="deny")

    requested, denied = last(app, "time_off.requested"), last(app, "time_off.denied")
    assert requested["actor"] == "casey@peoplebase.test"
    assert requested["details"] == {"kind": "vacation", "first_day": "2026-12-21", "last_day": "2026-12-24"}
    assert denied["actor"] == "riley@peoplebase.test"
    assert denied["subject"] == "Casey Example"
    assert denied["subject_id"] == request_id


def test_department_changes_are_recorded(app, client):
    post(client, "/departments", name="Finance")
    post(client, "/departments/1/rename", name="Finance and Legal")
    post(client, "/departments/1/delete")

    assert last(app, "department.created")["subject"] == "Finance"
    assert last(app, "department.renamed")["details"] == {"name": ["Finance", "Finance and Legal"]}
    assert last(app, "department.deleted")["subject"] == "Finance and Legal"


def test_exporting_pay_data_is_recorded(app, client):
    client.get("/employees.csv?q=avery")

    entry = last(app, "export.directory_csv")
    assert entry["actor"] == HR_EMAIL
    assert entry["details"] == {"rows": 1, "filters": {"search": "avery"}}


def test_own_password_change_is_recorded_without_the_password(app, client):
    post(
        client, "/account/password",
        current_password=TEST_PASSWORD, new_password="four unrelated plain words",
        confirm_password="four unrelated plain words",
    )

    assert last(app, "auth.password_changed")["actor"] == HR_EMAIL
    everything = json.dumps(entries(app), default=str)
    assert TEST_PASSWORD not in everything
    assert "four unrelated plain words" not in everything
    assert "$argon2" not in everything


def test_create_admin_command_is_recorded_as_the_command_line(app):
    app.test_cli_runner().invoke(
        args=["create-admin", "--email", "second@peoplebase.test", "--password", "four unrelated plain words"]
    )

    entry = last(app, "account.created")
    assert entry["actor"] == "command line"
    assert entry["actor_id"] is None


def test_refused_changes_leave_no_entry(app, client, staff, sign_in):
    before = len(entries(app))

    post(client, "/employees/new", name="", position="", salary="")
    post(client, "/departments", name="")
    casey = sign_in("casey@peoplebase.test")
    writes_before_refusal = len(entries(app))
    post(casey, f"/employees/{staff['casey']}/edit", **{**EDIT, "salary": "999999"})

    assert writes_before_refusal == before + 1  # only Casey signing in
    assert len(entries(app)) == writes_before_refusal


def test_unknown_actions_cannot_be_logged(app):
    with app.app_context(), pytest.raises(ValueError):
        audit("employee.vanished")


def test_entry_and_change_are_saved_together_or_not_at_all(app):
    with app.app_context():
        employee = db.session.get(Employee, 1)
        employee.name = "Changed Example"
        audit("employee.updated", employee, name=["Avery Example", "Changed Example"])
        db.session.rollback()

        assert db.session.get(Employee, 1).name == "Avery Example"
        assert db.session.scalar(db.select(db.func.count(AuditEvent.id))) == 0


# Append-only


@pytest.fixture()
def logged(app, client):
    """One real entry to try to tamper with."""
    post(client, "/departments", name="Finance")
    return last(app, "department.created")


def test_entries_cannot_be_edited(app, logged):
    with app.app_context():
        entry = db.session.scalar(db.select(AuditEvent).where(AuditEvent.action == "department.created"))
        entry.actor_label = "someone.else@peoplebase.test"

        with pytest.raises(AuditLogError):
            db.session.commit()
        db.session.rollback()

    assert last(app, "department.created")["actor"] == HR_EMAIL


def test_entries_cannot_be_deleted(app, logged):
    with app.app_context():
        db.session.delete(db.session.scalar(db.select(AuditEvent)))

        with pytest.raises(AuditLogError):
            db.session.commit()
        db.session.rollback()

    assert entries(app, "department.created")


@pytest.mark.parametrize(
    "statement",
    [
        lambda: db.update(AuditEvent).values(actor_label="nobody"),
        lambda: db.delete(AuditEvent),
        lambda: db.delete(AuditEvent).where(AuditEvent.action == "department.created"),
        lambda: AuditEvent.__table__.delete(),
    ],
)
def test_bulk_statements_against_the_log_are_refused(app, logged, statement):
    with app.app_context():
        with pytest.raises(AuditLogError):
            db.session.execute(statement())
        db.session.rollback()

    assert last(app, "department.created")["actor"] == HR_EMAIL


def test_guard_covers_every_session_not_just_the_request_one(app, logged):
    with app.app_context(), Session(db.engine) as other:
        entry = other.scalar(db.select(AuditEvent))
        entry.subject_label = "tampered"

        with pytest.raises(AuditLogError):
            other.commit()


def test_no_route_writes_to_the_log_directly(app, client):
    for method in (client.post, client.put, client.patch, client.delete):
        assert method("/audit", data={"csrf_token": csrf_from(client, "/")}).status_code == 405
    rules = [rule.rule for rule in app.url_map.iter_rules() if rule.rule.startswith("/audit")]
    assert rules == ["/audit"]


# Reading the log


@pytest.mark.parametrize("who", ["casey", "riley", "morgan"])
def test_only_hr_reads_the_log(staff, sign_in, who):
    response = sign_in(f"{who}@peoplebase.test").get("/audit")

    assert response.status_code == 403
    assert HR_EMAIL.encode() not in response.data


def test_log_page_shows_newest_first_with_readable_details(app, client, staff):
    post(
        client, f"/employees/{staff['casey']}/edit",
        **{**EDIT, "salary": "141000", "manager_id": str(staff["riley"]), "department_id": "1", "reason": "Raise"},
    )
    post(client, "/departments", name="Finance")

    page = client.get("/audit").data.decode()

    assert page.index("Added department") < page.index("Changed pay or title") < page.index("<td>Signed in</td>")
    assert "<del>128000.00</del> <ins>141000.00</ins>" in page
    assert "Casey Example" in page


def test_log_filters_by_kind_and_person(app, client, staff, sign_in):
    sign_in("casey@peoplebase.test")
    post(client, "/departments", name="Finance")

    departments = client.get("/audit?group=department").data
    casey = client.get("/audit?q=CASEY").data
    nothing = client.get("/audit?q=100%25_nobody").data
    ignored = client.get("/audit?group=nonsense").data

    assert b"Added department" in departments and b"<td>Signed in</td>" not in departments
    assert b"casey@peoplebase.test" in casey and b"Added department" not in casey
    assert b"No entries match." in nothing
    assert b"Added department" in ignored


def test_log_is_paged_fifty_at_a_time(app, client):
    with app.app_context():
        for number in range(60):
            audit("department.created", label=f"Department {number:02d}")
        db.session.commit()

    first = client.get("/audit").data
    second = client.get("/audit?page=2").data
    huge = client.get(f"/audit?page={10**30}")

    assert first.count(b"<tr>") == 51 and b"Page 1 of 2" in first
    assert b"Department 59" in first and b"Department 00" not in first
    assert b"Department 00" in second
    assert huge.status_code == 200


def test_every_action_has_a_label_and_a_group():
    assert all(action.split(".")[0] in AUDIT_GROUPS for action in AUDIT_ACTIONS)


def test_audit_details_are_escaped(app, client):
    post(client, "/departments", name="<script>alert(1)</script>")

    page = client.get("/audit").data

    assert b"<script>alert(1)</script>" not in page
    assert b"&lt;script&gt;" in page


def test_dates_in_details_are_iso_strings(app):
    with app.app_context():
        audit("time_off.requested", label="Sample", first_day=date(2026, 12, 21))
        db.session.commit()

    assert last(app, "time_off.requested")["details"] == {"first_day": "2026-12-21"}
