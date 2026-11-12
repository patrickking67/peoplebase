"""Leave balances: the arithmetic in leave.py, then where the balances show."""

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app as app_module
from app import Employee, TimeOffRequest, db, format_days
from leave import (
    LEAVE_ALLOWANCES,
    leave_balance,
    leave_balances,
    months_employed,
    request_shortfall,
    workdays,
    workdays_in_year,
)
from tests.conftest import csrf_from

TODAY = date(2026, 11, 11)  # a Wednesday


def off(kind, start, end, status="approved"):
    return SimpleNamespace(kind=kind, start_date=start, end_date=end, status=status)


@pytest.fixture()
def frozen_today(monkeypatch):
    class FrozenDate(date):
        @classmethod
        def today(cls):
            return cls(TODAY.year, TODAY.month, TODAY.day)

    monkeypatch.setattr(app_module, "date", FrozenDate)
    return TODAY


def post(client, path, **data):
    return client.post(path, data={"csrf_token": csrf_from(client, "/"), **data}, follow_redirects=True)


# Working days


@pytest.mark.parametrize(
    "start,end,expected",
    [
        (date(2026, 11, 9), date(2026, 11, 13), 5),    # Monday to Friday
        (date(2026, 11, 9), date(2026, 11, 9), 1),     # one weekday
        (date(2026, 11, 14), date(2026, 11, 15), 0),   # a weekend
        (date(2026, 11, 13), date(2026, 11, 16), 2),   # Friday to Monday
        (date(2026, 11, 7), date(2026, 11, 22), 10),   # Saturday through two full weeks
        (date(2026, 12, 21), date(2027, 1, 3), 10),    # across New Year
        (date(2026, 11, 13), date(2026, 11, 9), 0),    # backwards
    ],
)
def test_workdays_count_monday_to_friday(start, end, expected):
    assert workdays(start, end) == expected


def test_workdays_match_a_day_by_day_count_for_every_span_in_a_quarter():
    first = date(2026, 10, 1)
    for start_offset in range(0, 92, 3):
        for length in range(0, 61, 7):
            start = first + timedelta(days=start_offset)
            end = start + timedelta(days=length)
            slow = sum(1 for n in range(length + 1) if (start + timedelta(days=n)).weekday() < 5)
            assert workdays(start, end) == slow


def test_a_request_across_new_year_is_split_between_the_years():
    start, end = date(2026, 12, 28), date(2027, 1, 8)

    assert workdays_in_year(start, end, 2026) == 4
    assert workdays_in_year(start, end, 2027) == 6
    assert workdays_in_year(start, end, 2028) == 0


# Accrual


@pytest.mark.parametrize(
    "start_date,through,expected",
    [
        (None, 11, 11),                 # no start date on record: treated as here all year
        (date(2019, 5, 1), 11, 11),     # started in an earlier year
        (date(2026, 1, 1), 1, 1),       # January counts on January 1
        (date(2026, 8, 20), 11, 4),     # joined in August: Aug, Sep, Oct, Nov
        (date(2026, 11, 30), 11, 1),    # joined on the last day of the month
        (date(2026, 12, 1), 11, 0),     # starts next month
        (date(2027, 2, 1), 12, 0),      # starts next year
    ],
)
def test_months_employed(start_date, through, expected):
    assert months_employed(2026, start_date, through) == expected


def test_vacation_accrues_a_twelfth_each_month():
    balance = leave_balance("vacation", 2026, TODAY, date(2020, 1, 6), [])

    assert balance.entitlement == Decimal("20.00")
    assert balance.accrued == Decimal("18.33")  # 11 of 12 months
    assert balance.available == Decimal("18.33")


def test_accrual_is_complete_in_december_and_for_past_years_and_zero_for_future_ones():
    assert leave_balance("vacation", 2026, date(2026, 12, 1), None, []).accrued == Decimal("20.00")
    assert leave_balance("vacation", 2025, TODAY, None, []).accrued == Decimal("20.00")
    assert leave_balance("vacation", 2027, TODAY, None, []).accrued == Decimal("0.00")
    assert leave_balance("vacation", 2027, TODAY, None, []).entitlement == Decimal("20.00")


def test_mid_year_joiner_gets_the_months_from_their_start():
    joined = date(2026, 8, 20)

    vacation = leave_balance("vacation", 2026, TODAY, joined, [])
    sick = leave_balance("sick", 2026, TODAY, joined, [])
    personal = leave_balance("personal", 2026, TODAY, joined, [])

    assert (vacation.entitlement, vacation.accrued) == (Decimal("8.33"), Decimal("6.67"))
    assert (sick.entitlement, sick.accrued) == (Decimal("4.17"), Decimal("3.33"))
    assert (personal.entitlement, personal.accrued) == (Decimal("1.25"), Decimal("1.25"))


def test_personal_days_arrive_in_full_at_the_start_of_the_year():
    january = leave_balance("personal", 2026, date(2026, 1, 1), date(2020, 1, 6), [])

    assert january.accrued == january.entitlement == Decimal("3.00")
    assert not january.accrues_monthly


def test_someone_who_has_not_started_yet_has_nothing():
    for kind in LEAVE_ALLOWANCES:
        assert leave_balance(kind, 2026, TODAY, date(2026, 12, 1), []).accrued == Decimal("0.00")


# Used and pending


def test_approved_is_used_pending_is_held_and_denied_is_ignored():
    requests = [
        off("vacation", date(2026, 3, 2), date(2026, 3, 6)),                     # 5 used
        off("vacation", date(2026, 12, 21), date(2026, 12, 24), "pending"),      # 4 held
        off("vacation", date(2026, 6, 1), date(2026, 6, 30), "denied"),
        off("sick", date(2026, 4, 1), date(2026, 4, 2)),
    ]

    vacation, sick, personal = leave_balances(2026, TODAY, date(2020, 1, 6), requests)

    assert (vacation.used, vacation.pending, vacation.available) == (5, 4, Decimal("9.33"))
    assert (sick.used, sick.pending) == (2, 0)
    assert (personal.used, personal.available) == (0, Decimal("3.00"))


def test_weekends_inside_a_request_do_not_use_leave():
    requests = [off("vacation", date(2026, 3, 6), date(2026, 3, 9))]  # Friday to Monday

    assert leave_balance("vacation", 2026, TODAY, None, requests).used == 2


def test_only_the_days_inside_the_year_count_for_that_year():
    requests = [off("vacation", date(2026, 12, 28), date(2027, 1, 8))]

    assert leave_balance("vacation", 2026, TODAY, None, requests).used == 4
    assert leave_balance("vacation", 2027, date(2027, 1, 15), None, requests).used == 6


def test_available_goes_negative_when_requests_run_ahead_of_accrual():
    requests = [off("vacation", date(2026, 2, 2), date(2026, 2, 13))]  # 10 days in February

    balance = leave_balance("vacation", 2026, date(2026, 2, 1), None, requests)

    assert balance.accrued == Decimal("3.33")
    assert balance.available == Decimal("-6.67")


# Shortfall on one request


def test_shortfall_is_zero_inside_the_balance():
    request = off("vacation", date(2026, 12, 21), date(2026, 12, 24), "pending")

    assert request_shortfall(request, TODAY, date(2020, 1, 6), [request]) == 0


def test_shortfall_reads_accrual_on_the_first_day_of_the_leave_not_today():
    used = off("vacation", date(2026, 3, 2), date(2026, 3, 26))               # 19 days
    request = off("vacation", date(2026, 12, 21), date(2026, 12, 21), "pending")

    # In November 18.33 has accrued, so 20 booked days would be 1.67 short.
    # By the December start date all 20 have accrued and nothing is short.
    assert request_shortfall(request, TODAY, date(2020, 1, 6), [used, request]) == 0
    earlier = off("vacation", date(2026, 11, 20), date(2026, 11, 20), "pending")
    assert request_shortfall(earlier, TODAY, date(2020, 1, 6), [used, earlier]) == Decimal("1.67")


def test_shortfall_counts_other_pending_requests():
    first = off("personal", date(2026, 11, 16), date(2026, 11, 17), "pending")
    second = off("personal", date(2026, 11, 23), date(2026, 11, 24), "pending")

    assert request_shortfall(second, TODAY, None, [first, second]) == Decimal("1.00")


def test_next_years_request_is_checked_against_next_year():
    request = off("vacation", date(2027, 1, 4), date(2027, 1, 8), "pending")  # 5 days in January

    assert request_shortfall(request, TODAY, None, [request]) == Decimal("3.33")  # 1.67 accrued by then


@pytest.mark.parametrize(
    "value,text", [("18.33", "18.33"), ("1.50", "1.5"), ("20.00", "20"), ("0", "0"), ("-6.67", "-6.67"), ("-0.00", "0")]
)
def test_days_are_shown_without_trailing_zeros(value, text):
    assert format_days(Decimal(value)) == text


# Where balances show


@pytest.fixture()
def casey_leave(app, staff, frozen_today):
    with app.app_context():
        casey = db.session.get(Employee, staff["casey"])
        casey.start_date = date(2024, 2, 5)
        db.session.add_all(
            [
                TimeOffRequest(
                    employee=casey, kind="vacation", start_date=date(2026, 3, 2), end_date=date(2026, 3, 6),
                    status="approved",
                ),
                TimeOffRequest(
                    employee=casey, kind="vacation", start_date=date(2026, 11, 23), end_date=date(2026, 11, 24),
                ),
            ]
        )
        db.session.commit()
    return staff["casey"]


def balance_row(page, label):
    """The numbers in one row of the balance table."""
    row = page.decode().split(f'<th scope="row">{label}</th>')[1].split("</tr>")[0]
    return [cell.split(">")[-1] for cell in row.replace("<strong>", "").replace("</strong>", "").split("</td>")[:-1]]


def test_profile_shows_balances_with_accrual(app, sign_in, casey_leave):
    page = sign_in("casey@peoplebase.test").get(f"/employees/{casey_leave}").data

    assert b"Leave balances, 2026" in page
    # Available, then how it is reached: accrued, used, pending, and the year's allowance.
    assert balance_row(page, "Vacation") == ["11.33", "18.33", "5", "2", "20"]
    assert balance_row(page, "Sick leave") == ["9.17", "9.17", "0", "0", "10"]
    assert balance_row(page, "Personal") == ["3", "3", "0", "0", "3"]


def test_balances_follow_profile_access(app, sign_in, casey_leave):
    for who, visible in [("riley", True), ("emerson", True), ("jordan", False), ("harper", False)]:
        response = sign_in(f"{who}@peoplebase.test").get(f"/employees/{casey_leave}")
        assert (b"Leave balances, 2026" in response.data) is visible


def test_dashboard_and_request_form_show_your_own_balances(app, sign_in, casey_leave):
    casey = sign_in("casey@peoplebase.test")

    assert balance_row(casey.get("/").data, "Vacation") == ["11.33", "18.33", "5", "2", "20"]
    assert balance_row(casey.get("/time-off/new").data, "Vacation")[0] == "11.33"
    assert b"Your leave, 2026" not in sign_in("emerson@peoplebase.test").get("/time-off/new").data


def test_people_who_left_have_no_balance_panel(app, client, casey_leave):
    with app.app_context():
        db.session.get(Employee, casey_leave).status = "terminated"
        db.session.commit()

    assert b"Leave balances" not in client.get(f"/employees/{casey_leave}").data


def test_request_past_the_balance_is_accepted_with_a_note_for_the_approver(app, sign_in, casey_leave):
    casey = sign_in("casey@peoplebase.test")

    response = post(
        casey, "/time-off/new", kind="vacation", start_date="2026-11-25", end_date="2026-12-11"
    )  # 13 working days; 20 accrued by December is not what counts, November's 18.33 is

    assert b"Requested 17 days off for Casey Example." in response.data
    assert b"This is 1.67 working days more vacation than will have accrued by then." in response.data
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(TimeOffRequest.id))) == 3

    queue = sign_in("riley@peoplebase.test").get("/time-off?status=pending").data
    assert queue.count(b"1.67 working days more vacation than Casey will have accrued by then.") == 2


def test_request_inside_the_balance_has_no_note(app, sign_in, casey_leave):
    casey = sign_in("casey@peoplebase.test")

    response = post(casey, "/time-off/new", kind="sick", start_date="2026-11-12", end_date="2026-11-12")

    assert b"Requested 1 day off" in response.data
    assert b"more sick leave than" not in response.data
    assert b"will have accrued by then" not in sign_in("riley@peoplebase.test").get("/time-off").data


def test_time_off_request_reports_its_working_days(app, staff):
    with app.app_context():
        item = TimeOffRequest(
            employee_id=staff["casey"], kind="vacation", start_date=date(2026, 11, 13), end_date=date(2026, 11, 16)
        )
        assert (item.days, item.workdays) == (4, 2)
