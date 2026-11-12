"""Onboarding and offboarding checklists, and the last working day."""

from datetime import date

import pytest

from app import ChecklistTask, Employee, db
from models import CHECKLIST_TEMPLATES, checklist_progress
from tests.conftest import csrf_from
from tests.test_audit import entries


def post(client, path, **data):
    return client.post(path, data={"csrf_token": csrf_from(client, "/"), **data})


def tasks(app, employee_id, kind):
    with app.app_context():
        return [
            (task.id, task.title, task.done, task.completed_by)
            for task in db.session.get(Employee, employee_id).checklist(kind)
        ]


def edit(client, employee_id, **overrides):
    form = {"name": "Casey Example", "position": "Engineer", "salary": "128000", "status": "active"}
    return post(client, f"/employees/{employee_id}/edit", **{**form, **overrides})


@pytest.fixture()
def onboarding(app, staff):
    """Casey with a fresh onboarding checklist. Returns the task ids."""
    with app.app_context():
        casey = db.session.get(Employee, staff["casey"])
        casey.start_checklist("onboarding")
        db.session.commit()
        return [task.id for task in casey.checklist("onboarding")]


# Starting checklists


def test_a_new_hire_gets_the_onboarding_checklist(app, client):
    post(client, "/employees/new", name="Quinn Example", position="Designer", salary="112000")

    assert [title for _, title, _, _ in tasks(app, 2, "onboarding")] == CHECKLIST_TEMPLATES["onboarding"]
    assert all(not done for _, _, done, _ in tasks(app, 2, "onboarding"))
    assert tasks(app, 2, "offboarding") == []


def test_marking_someone_as_left_records_the_last_day_and_starts_offboarding(app, client, staff):
    edit(client, staff["casey"], status="terminated", end_date="2026-11-30")

    with app.app_context():
        assert db.session.get(Employee, staff["casey"]).end_date == date(2026, 11, 30)
    assert [title for _, title, _, _ in tasks(app, staff["casey"], "offboarding")] == CHECKLIST_TEMPLATES["offboarding"]


def test_last_day_defaults_to_today_and_is_kept_on_later_edits(app, client, staff):
    edit(client, staff["casey"], status="terminated")
    with app.app_context():
        assert db.session.get(Employee, staff["casey"]).end_date == date.today()

    edit(client, staff["casey"], status="terminated", end_date="2026-10-30")
    edit(client, staff["casey"], status="terminated", position="Engineer II", end_date="")

    with app.app_context():
        assert db.session.get(Employee, staff["casey"]).end_date == date(2026, 10, 30)
    assert len(tasks(app, staff["casey"], "offboarding")) == 5  # not duplicated by the second save


@pytest.mark.parametrize(
    "fields,error",
    [
        ({"end_date": "2026-02-30"}, b"real calendar date"),
        ({"end_date": "30/11/2026"}, b"YYYY-MM-DD"),
        ({"start_date": "2026-06-01", "end_date": "2026-05-31"}, b"cannot be before the start date"),
    ],
)
def test_invalid_last_days_are_rejected(app, client, staff, fields, error):
    response = edit(client, staff["casey"], status="terminated", **fields)

    assert response.status_code == 400
    assert error in response.data
    with app.app_context():
        assert db.session.get(Employee, staff["casey"]).status == "active"


def test_last_day_is_ignored_for_people_still_on_staff(app, client, staff):
    edit(client, staff["casey"], status="active", end_date="2026-11-30")

    with app.app_context():
        assert db.session.get(Employee, staff["casey"]).end_date is None
    assert tasks(app, staff["casey"], "offboarding") == []


def test_coming_back_clears_the_last_day_and_the_old_offboarding_list(app, client, staff):
    edit(client, staff["casey"], status="terminated", end_date="2026-11-30")
    edit(client, staff["casey"], status="active")

    with app.app_context():
        assert db.session.get(Employee, staff["casey"]).end_date is None
    assert tasks(app, staff["casey"], "offboarding") == []


# Working through a checklist


def test_hr_completes_and_reopens_a_task(app, client, staff, onboarding):
    post(client, f"/checklists/{onboarding[0]}", done="1")

    _, title, done, completed_by = tasks(app, staff["casey"], "onboarding")[0]
    assert (title, done, completed_by) == ("Signed contract on file", True, "hr@peoplebase.test")

    post(client, f"/checklists/{onboarding[0]}", done="0")
    assert tasks(app, staff["casey"], "onboarding")[0][2:] == (False, None)


@pytest.mark.parametrize(
    "who,status,done",
    [
        ("riley", 302, True),      # direct manager
        ("morgan", 302, True),     # manager's manager
        ("emerson", 302, True),    # HR
        ("casey", 403, False),     # their own checklist: view only
        ("jordan", 404, False),    # a peer cannot even tell the task exists
        ("harper", 404, False),    # another team's manager
    ],
)
def test_who_can_tick_a_task(app, staff, sign_in, onboarding, who, status, done):
    response = post(sign_in(f"{who}@peoplebase.test"), f"/checklists/{onboarding[1]}", done="1")

    assert response.status_code == status
    assert tasks(app, staff["casey"], "onboarding")[1][2] is done


def test_checklist_updates_need_csrf_and_post(app, client, staff, onboarding):
    assert client.post(f"/checklists/{onboarding[0]}", data={"done": "1"}).status_code == 400
    assert client.get(f"/checklists/{onboarding[0]}").status_code == 405
    assert post(client, "/checklists/99999", done="1").status_code == 404
    assert tasks(app, staff["casey"], "onboarding")[0][2] is False


def test_ticking_returns_to_a_local_page_only(client, onboarding, staff):
    back = post(client, f"/checklists/{onboarding[0]}", done="1", next="/checklists")
    away = post(client, f"/checklists/{onboarding[1]}", done="1", next="https://evil.example/")

    assert back.headers["Location"] == "/checklists"
    assert away.headers["Location"] == f"/employees/{staff['casey']}"


def test_ticks_are_audited_once(app, client, onboarding):
    post(client, f"/checklists/{onboarding[0]}", done="1")
    post(client, f"/checklists/{onboarding[0]}", done="1")  # already done: nothing new to record
    post(client, f"/checklists/{onboarding[0]}", done="0")

    completed, reopened = entries(app, "checklist.completed"), entries(app, "checklist.reopened")
    assert len(completed) == len(reopened) == 1
    assert completed[0]["subject"] == "Casey Example"
    assert completed[0]["details"] == {"task": "Signed contract on file", "checklist": "onboarding"}


# Seeing checklists


def test_profile_shows_the_checklist_and_progress(app, client, staff, sign_in, onboarding):
    post(client, f"/checklists/{onboarding[0]}", done="1")

    own = sign_in("casey@peoplebase.test").get(f"/employees/{staff['casey']}").data
    manager = sign_in("riley@peoplebase.test").get(f"/employees/{staff['casey']}").data

    for page in (own, manager):
        assert b"In progress: 1 of 5" in page
        assert b"Laptop and accounts ready" in page
    assert b"Mark done" in manager and b"Mark done" not in own
    assert b"hr@peoplebase.test" in manager and b"hr@peoplebase.test" not in own


def test_open_checklists_page_is_scoped_to_reach(app, staff, sign_in, onboarding):
    with app.app_context():
        db.session.get(Employee, staff["rowan"]).start_checklist("onboarding")
        db.session.commit()

    riley = sign_in("riley@peoplebase.test").get("/checklists").data
    harper = sign_in("harper@peoplebase.test").get("/checklists").data
    hr = sign_in("emerson@peoplebase.test").get("/checklists").data

    assert b"Casey Example" in riley and b"Rowan Example" not in riley
    assert b"Rowan Example" in harper and b"Casey Example" not in harper
    assert b"Casey Example" in hr and b"Rowan Example" in hr
    assert sign_in("casey@peoplebase.test").get("/checklists").status_code == 403


def test_finished_checklists_leave_the_open_page(app, client, staff, onboarding):
    for task_id in onboarding:
        post(client, f"/checklists/{task_id}", done="1")

    assert b"Nothing open." in client.get("/checklists").data
    assert b"Complete: 5 of 5" in client.get(f"/employees/{staff['casey']}").data


def test_deleting_an_employee_deletes_their_tasks(app, client, staff, onboarding):
    post(client, f"/employees/{staff['casey']}/delete")

    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(ChecklistTask.id))) == 0


def test_progress_labels():
    done, open_ = type("Task", (), {"done": True})(), type("Task", (), {"done": False})()

    assert checklist_progress([]) == (0, 0, "Not started")
    assert checklist_progress([open_, open_]) == (0, 2, "Not started")
    assert checklist_progress([done, open_]) == (1, 2, "In progress")
    assert checklist_progress([done, done]) == (2, 2, "Complete")


def test_demo_data_has_one_person_onboarding_and_one_offboarded(app):
    with app.app_context():
        db.session.delete(db.session.get(Employee, 1))
        db.session.commit()
    app.test_cli_runner().invoke(args=["seed-demo"])

    with app.app_context():
        skyler = db.session.scalar(db.select(Employee).where(Employee.name == "Skyler Example"))
        parker = db.session.scalar(db.select(Employee).where(Employee.name == "Parker Example"))
        assert checklist_progress(skyler.checklist("onboarding")) == (3, 5, "In progress")
        assert checklist_progress(parker.checklist("offboarding")) == (4, 5, "In progress")
        assert parker.end_date is not None and parker.end_date < date.today()
