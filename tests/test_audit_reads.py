"""Sensitive reads are logged. Abuse case 18 in docs/security.md: an insider
browsing records, pay or the audit log should leave a trace."""

import pytest

from tests.conftest import HR_EMAIL
from tests.test_audit import entries, last


def views(app):
    return entries(app, "view.employee")


def test_hr_reading_someone_elses_record_is_logged_with_pay_shown(app, client, staff):
    client.get(f"/employees/{staff['casey']}")

    entry = last(app, "view.employee")
    assert entry["actor"] == HR_EMAIL
    assert entry["subject_id"] == staff["casey"]
    assert entry["details"] == {"pay_shown": True}


def test_manager_reading_a_report_is_logged_without_pay(app, sign_in, staff):
    sign_in("riley@peoplebase.test").get(f"/employees/{staff['casey']}")

    entry = last(app, "view.employee")
    assert entry["actor"] == "riley@peoplebase.test"
    assert entry["details"] == {"pay_shown": False}


def test_reading_your_own_record_is_not_logged(app, sign_in, staff):
    sign_in("casey@peoplebase.test").get(f"/employees/{staff['casey']}")
    sign_in("riley@peoplebase.test").get(f"/employees/{staff['riley']}")

    assert views(app) == []


@pytest.mark.parametrize(
    ("viewer", "target"),
    [("casey", "jordan"), ("casey", "morgan"), ("riley", "rowan"), ("harper", "casey")],
)
def test_refused_reads_are_404_and_leave_no_view_entry(app, sign_in, staff, viewer, target):
    response = sign_in(f"{viewer}@peoplebase.test").get(f"/employees/{staff[target]}")

    assert response.status_code == 404
    assert views(app) == []


def test_hr_directory_view_is_logged_but_other_roles_are_not(app, client, sign_in, staff):
    sign_in("riley@peoplebase.test").get("/employees")
    sign_in("casey@peoplebase.test").get("/employees")
    assert entries(app, "view.directory") == []

    client.get("/employees")

    entry = last(app, "view.directory")
    assert entry["actor"] == HR_EMAIL
    assert entry["details"]["matching"] >= 1


def test_audit_log_views_are_logged_and_only_for_hr(app, client, sign_in, staff):
    assert sign_in("riley@peoplebase.test").get("/audit").status_code == 403
    assert entries(app, "view.audit") == []

    client.get("/audit?page=2")

    entry = last(app, "view.audit")
    assert entry["actor"] == HR_EMAIL
    assert entry["details"] == {"page": 2}


def test_view_entries_can_be_filtered_and_are_labelled(client, staff):
    client.get(f"/employees/{staff['casey']}")
    page = client.get("/audit?group=view").data.decode()

    assert "Viewed employee record" in page
    assert "Sensitive views" in page
