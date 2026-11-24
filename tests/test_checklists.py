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
    # Task 0 is owned by HR, so the employee's own checklist is view only for it.
    response = post(sign_in(f"{who}@peoplebase.test"), f"/checklists/{onboarding[0]}", done="1")

    assert response.status_code == status
    assert tasks(app, staff["casey"], "onboarding")[0][2] is done


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
    assert b"Mark done" in manager
    # The employee can only tick the one task that is theirs: payroll and tax details.
    assert own.count(b"Mark done") == 1
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


# Owners and due dates (abuse case 20 in docs/security.md)


def test_standard_tasks_get_owners_and_due_dates_from_the_start_date(app, client):
    post(client, "/employees/new", name="Quinn Example", position="Designer", salary="112000", start_date="2026-06-10")

    with app.app_context():
        rows = {t.title: (t.owner, t.due_date) for t in db.session.get(Employee, 2).checklist("onboarding")}
    assert rows["Signed contract on file"] == ("hr", date(2026, 6, 5))
    assert rows["Payroll and tax details collected"] == ("employee", date(2026, 6, 12))
    assert rows["First-week plan agreed with their manager"] == ("manager", date(2026, 6, 13))


def test_offboarding_due_dates_count_from_the_last_day(app, client, staff):
    edit(client, staff["casey"], status="terminated", end_date="2026-11-30")

    with app.app_context():
        casey = db.session.get(Employee, staff["casey"])
        rows = {t.title: (t.owner, t.due_date) for t in casey.checklist("offboarding")}
    assert rows["Handover notes written"] == ("employee", date(2026, 11, 25))
    assert rows["Final pay and unused leave settled"] == ("hr", date(2026, 12, 7))


def test_without_an_anchor_date_tasks_have_no_due_date(app, client):
    post(client, "/employees/new", name="Quinn Example", position="Designer", salary="112000")

    with app.app_context():
        assert all(t.due_date is None for t in db.session.get(Employee, 2).checklist("onboarding"))


def test_an_overdue_task_is_flagged_to_those_who_can_see_it(app, client, staff, sign_in):
    with app.app_context():
        casey = db.session.get(Employee, staff["casey"])
        casey.start_date = date(2020, 1, 6)
        casey.start_checklist("onboarding")
        db.session.commit()

    page = sign_in("riley@peoplebase.test").get(f"/employees/{staff['casey']}").data
    assert b"overdue" in page
    assert b"overdue" in client.get("/checklists").data


def test_an_employee_can_tick_a_task_that_is_theirs_but_never_reopen_it(app, staff, sign_in, onboarding):
    casey = sign_in("casey@peoplebase.test")
    payroll = onboarding[1]

    assert post(casey, f"/checklists/{payroll}", done="1").status_code == 302
    assert tasks(app, staff["casey"], "onboarding")[1][2] is True
    assert post(casey, f"/checklists/{payroll}", done="0").status_code == 403
    assert tasks(app, staff["casey"], "onboarding")[1][2] is True


@pytest.mark.parametrize("index", [0, 2, 3, 4])
def test_an_employee_cannot_tick_tasks_owned_by_hr_or_their_manager(app, staff, sign_in, onboarding, index):
    """Abuse case 20: tick off the HR or manager steps of your own onboarding,
    or of your offboarding access removal, by posting to the task id."""
    response = post(sign_in("casey@peoplebase.test"), f"/checklists/{onboarding[index]}", done="1")

    assert response.status_code == 403
    assert tasks(app, staff["casey"], "onboarding")[index][2] is False


def test_an_employee_cannot_tick_a_colleagues_task_even_when_it_is_employee_owned(app, staff, sign_in, onboarding):
    response = post(sign_in("jordan@peoplebase.test"), f"/checklists/{onboarding[1]}", done="1")

    assert response.status_code == 404
    assert tasks(app, staff["casey"], "onboarding")[1][2] is False


def test_the_owner_cannot_be_set_from_the_form(app, staff, sign_in, onboarding):
    post(sign_in("casey@peoplebase.test"), f"/checklists/{onboarding[0]}", done="1", owner="employee")

    with app.app_context():
        task = db.session.get(ChecklistTask, onboarding[0])
        assert (task.owner, task.done) == ("hr", False)
