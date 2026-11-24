"""The team calendar. Abuse case 21 in docs/security.md: reading another
team's leave, or learning a colleague's kind of leave, through the calendar."""

from datetime import date

import pytest

from app import Account, TimeOffRequest, db, hash_password, parse_month
from tests.conftest import TEST_PASSWORD

MONTH = "2026-05"


@pytest.fixture()
def leave(app, staff):
    """Put people on leave in May 2026. Returns nothing; read it through the page."""

    def add(person, start, end, kind="sick", status="approved"):
        with app.app_context():
            db.session.add(
                TimeOffRequest(
                    employee_id=staff[person], kind=kind, start_date=start, end_date=end, status=status
                )
            )
            db.session.commit()

    add("casey", date(2026, 5, 4), date(2026, 5, 5), "sick")
    add("jordan", date(2026, 5, 11), date(2026, 5, 11), "vacation")
    add("rowan", date(2026, 5, 18), date(2026, 5, 18), "personal")
    add("morgan", date(2026, 5, 20), date(2026, 5, 20), "vacation")
    add("harper", date(2026, 5, 25), date(2026, 5, 25), "vacation", status="pending")
    add("riley", date(2026, 5, 26), date(2026, 5, 26), "vacation", status="denied")
    return add


def page(client, month=MONTH, **query):
    response = client.get("/time-off/calendar", query_string={"month": month, **query})
    assert response.status_code == 200
    return response.data.decode()


def test_hr_sees_everyone_except_denied_requests(sign_in, leave):
    body = page(sign_in("emerson@peoplebase.test"))

    for name in ("Casey", "Jordan", "Rowan", "Morgan", "Harper"):
        assert f"{name} Example" in body
    assert "Riley Example" not in body  # denied


def test_an_employee_sees_only_their_own_team(sign_in, leave):
    body = page(sign_in("casey@peoplebase.test"))

    assert "Casey Example" in body and "Jordan Example" in body
    for other in ("Rowan Example", "Morgan Example", "Harper Example"):
        assert other not in body


def test_a_manager_sees_reports_manager_and_peers_but_not_another_teams_staff(sign_in, leave):
    body = page(sign_in("riley@peoplebase.test"))

    for name in ("Casey", "Jordan", "Morgan"):
        assert f"{name} Example" in body
    assert "Rowan Example" not in body  # reports to Harper, not to Riley or Riley's manager
    assert "Harper Example" not in body  # a peer's request is pending, so it is not shown


def test_colleagues_see_who_is_out_but_not_the_kind_of_leave(sign_in, leave):
    colleague = page(sign_in("jordan@peoplebase.test"))
    manager = page(sign_in("riley@peoplebase.test"))
    own = page(sign_in("casey@peoplebase.test"))

    assert "Casey Example" in colleague and "Sick leave" not in colleague
    assert "Sick leave" in manager
    assert "Sick leave" in own


def test_pending_requests_are_shown_only_to_those_who_may_see_the_detail(sign_in, leave):
    assert "Harper Example" not in page(sign_in("jordan@peoplebase.test"))
    assert "Harper Example" not in page(sign_in("casey@peoplebase.test"))
    assert "pending" in page(sign_in("morgan@peoplebase.test"))  # Harper reports to Morgan


@pytest.mark.parametrize("extra", [{"employee": "7"}, {"team": "all"}, {"scope": "company"}, {"manager_id": "1"}])
def test_query_parameters_cannot_widen_the_scope(sign_in, leave, extra):
    body = page(sign_in("casey@peoplebase.test"), **extra)

    assert "Rowan Example" not in body and "Morgan Example" not in body


def test_someone_with_no_employee_record_and_no_hr_role_sees_an_empty_calendar(app, sign_in, leave):
    with app.app_context():
        db.session.add(
            Account(
                email="nobody@peoplebase.test",
                role="employee",
                password_hash=hash_password(TEST_PASSWORD),
                must_change_password=False,
            )
        )
        db.session.commit()

    body = page(sign_in("nobody@peoplebase.test"))
    assert "Example" not in body.split("<main", 1)[1]


def test_calendar_needs_sign_in(anonymous):
    assert anonymous.get("/time-off/calendar").status_code == 302


@pytest.mark.parametrize("text", [None, "", "2026-13", "2026-5", "99999-01", "1999-12", "'; drop table", "2101-01"])
def test_a_bad_month_falls_back_to_this_month(text):
    assert parse_month(text, date(2026, 11, 24)) == (2026, 11)


def test_a_good_month_is_used():
    assert parse_month("2026-02", date(2026, 11, 24)) == (2026, 2)


def test_leave_spanning_a_month_boundary_shows_in_both_months(sign_in, leave):
    leave("jordan", date(2026, 6, 29), date(2026, 7, 2), "vacation")
    client = sign_in("jordan@peoplebase.test")

    assert "Jordan Example" in page(client, "2026-06")
    assert "Jordan Example" in page(client, "2026-07")
    assert "2026-07-02" in page(client, "2026-07")
