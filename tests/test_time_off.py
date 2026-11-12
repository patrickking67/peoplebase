from datetime import date

import pytest

from app import Employee, TimeOffRequest, db
from tests.conftest import csrf_from


@pytest.fixture()
def team(app):
    """Morgan manages Riley. Sam has no manager. Avery (id 1) is seeded too."""
    with app.app_context():
        morgan = Employee(name="Morgan Example", position="Director", salary=1)
        riley = Employee(name="Riley Example", position="Lead", salary=1, manager=morgan)
        sam = Employee(name="Sam Example", position="Analyst", salary=1)
        db.session.add_all([morgan, riley, sam])
        db.session.commit()
        return {"avery": 1, "morgan": morgan.id, "riley": riley.id, "sam": sam.id}


def request_form(csrf_token, who, **overrides):
    data = {
        "employee_id": str(who),
        "kind": "vacation",
        "start_date": "2026-12-21",
        "end_date": "2026-12-24",
        "note": "Family visit",
        "csrf_token": csrf_token,
    }
    data.update(overrides)
    return data


@pytest.fixture()
def pending(app, team):
    with app.app_context():
        item = TimeOffRequest(
            employee_id=team["riley"], kind="vacation", start_date=date(2026, 12, 21), end_date=date(2026, 12, 24)
        )
        db.session.add(item)
        db.session.commit()
        return item.id


def get_request(app, request_id):
    with app.app_context():
        item = db.session.get(TimeOffRequest, request_id)
        return {"status": item.status, "decided_by_id": item.decided_by_id, "decided_at": item.decided_at}


def request_count(app):
    with app.app_context():
        return db.session.scalar(db.select(db.func.count(TimeOffRequest.id)))


# Requesting


def test_time_off_can_be_requested(client, app, csrf_token, team):
    response = client.post(
        "/time-off/new", data=request_form(csrf_token, team["riley"]), follow_redirects=True
    )

    assert response.status_code == 200
    assert b"Requested 4 days off for Riley Example" in response.data
    with app.app_context():
        item = db.session.scalar(db.select(TimeOffRequest))
        assert item.status == "pending"
        assert item.days == 4
        assert item.note == "Family visit"


def test_single_day_request(client, app, csrf_token, team):
    response = client.post(
        "/time-off/new",
        data=request_form(csrf_token, team["sam"], start_date="2026-12-01", end_date="2026-12-01"),
        follow_redirects=True,
    )

    assert b"Requested 1 day off for Sam Example" in response.data


def test_request_form_preselects_employee(client, team):
    response = client.get(f"/time-off/new?employee={team['sam']}")

    assert f'<option value="{team["sam"]}" selected>'.encode() in response.data


@pytest.mark.parametrize(
    "overrides,error",
    [
        ({"employee_id": ""}, b"Choose who the time off is for"),
        ({"employee_id": "999"}, b"valid employee"),
        ({"kind": "sabbatical"}, b"Choose a type of time off"),
        ({"start_date": ""}, b"First day is required"),
        ({"end_date": "2026-13-01"}, b"real calendar date"),
        ({"start_date": "2026-12-24", "end_date": "2026-12-21"}, b"cannot be before the first day"),
        ({"start_date": "2026-01-01", "end_date": "2026-03-02"}, b"at most 60 days"),
        ({"note": "x" * 201}, b"200 characters"),
        ({"note": "line\nbreak"}, b"control characters"),
    ],
)
def test_invalid_requests_are_rejected(client, app, csrf_token, team, overrides, error):
    response = client.post("/time-off/new", data=request_form(csrf_token, team["riley"], **overrides))

    assert response.status_code == 400
    assert error in response.data
    assert request_count(app) == 0


def test_terminated_employees_cannot_request(client, app, csrf_token, team):
    with app.app_context():
        db.session.get(Employee, team["sam"]).status = "terminated"
        db.session.commit()

    response = client.post("/time-off/new", data=request_form(csrf_token, team["sam"]))

    assert response.status_code == 400
    assert b"no longer works here" in response.data


def test_overlapping_requests_are_rejected(client, app, csrf_token, pending, team):
    response = client.post(
        "/time-off/new",
        data=request_form(csrf_token, team["riley"], start_date="2026-12-24", end_date="2026-12-26"),
    )

    assert response.status_code == 400
    assert b"already has pending time off" in response.data
    assert request_count(app) == 1


def test_denied_requests_do_not_block_new_ones(client, app, csrf_token, pending, team):
    with app.app_context():
        db.session.get(TimeOffRequest, pending).status = "denied"
        db.session.commit()

    response = client.post("/time-off/new", data=request_form(csrf_token, team["riley"]))

    assert response.status_code == 302
    assert request_count(app) == 2


def test_time_off_requires_csrf(client, app, team, pending):
    data = request_form("", team["riley"])
    del data["csrf_token"]

    assert client.post("/time-off/new", data=data).status_code == 400
    assert client.post(f"/time-off/{pending}/decide", data={"decision": "approve"}).status_code == 400
    assert get_request(app, pending)["status"] == "pending"


# Deciding. The signed-in account decides; tests/test_access.py covers who may.


def decide(client, request_id, decision="approve", **extra):
    return client.post(
        f"/time-off/{request_id}/decide",
        data={"decision": decision, "csrf_token": csrf_from(client, "/time-off"), **extra},
        follow_redirects=True,
    )


def test_hr_can_approve(client, app, pending):
    response = decide(client, pending)

    assert b"Approved Riley Example&#39;s time off from 2026-12-21" in response.data
    item = get_request(app, pending)
    assert item["status"] == "approved"
    assert item["decided_at"] is not None


def test_hr_can_deny(client, app, pending):
    decide(client, pending, "deny")

    assert get_request(app, pending)["status"] == "denied"


def test_decision_records_the_deciding_employee(app, sign_in, staff):
    with app.app_context():
        item = TimeOffRequest(
            employee_id=staff["casey"], kind="vacation", start_date=date(2026, 12, 21), end_date=date(2026, 12, 24)
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    decide(sign_in("riley@peoplebase.test"), item_id)

    assert get_request(app, item_id)["decided_by_id"] == staff["riley"]


def test_invalid_decision_is_rejected(client, app, pending):
    response = decide(client, pending, "maybe")

    assert b"Choose approve or deny" in response.data
    assert get_request(app, pending)["status"] == "pending"


def test_decided_requests_cannot_be_decided_again(client, app, pending):
    decide(client, pending, "approve")

    response = decide(client, pending, "deny")

    assert b"already approved" in response.data
    assert get_request(app, pending)["status"] == "approved"


def test_deciding_a_missing_request_is_404(client, csrf_token):
    response = client.post("/time-off/999/decide", data={"decision": "approve", "csrf_token": csrf_token})

    assert response.status_code == 404


def test_decision_requires_post(client, pending):
    assert client.get(f"/time-off/{pending}/decide").status_code == 405


# Listing and profile


def test_list_shows_requests_and_filters_by_status(client, app, pending, team):
    everything = client.get("/time-off")
    approved_only = client.get("/time-off?status=approved")

    assert b"Riley Example" in everything.data
    assert b'value="approve"' in everything.data
    assert b"Riley Example" not in approved_only.data
    assert b"No approved requests" in approved_only.data


def test_decision_form_has_no_approver_field(client, pending):
    response = client.get("/time-off")

    assert b"approver_id" not in response.data
    assert b"Decided by" not in response.data


def test_profile_lists_time_off(client, pending, team):
    response = client.get(f"/employees/{team['riley']}")

    assert b"Vacation, 2026-12-21 to 2026-12-24 (4 days)" in response.data


def test_deleting_people_removes_or_keeps_their_requests(client, app, csrf_token, pending, team):
    with app.app_context():
        item = db.session.get(TimeOffRequest, pending)
        item.status = "approved"
        item.decided_by_id = team["morgan"]
        db.session.commit()
    assert b"Approved by Morgan Example." in client.get("/time-off").data

    client.post(f"/employees/{team['morgan']}/delete", data={"csrf_token": csrf_token})
    item = get_request(app, pending)
    assert item["status"] == "approved"
    assert item["decided_by_id"] is None
    assert b"Approved." in client.get("/time-off").data

    client.post(f"/employees/{team['riley']}/delete", data={"csrf_token": csrf_token})
    assert request_count(app) == 0
