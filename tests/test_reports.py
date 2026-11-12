"""Headcount and attrition: the arithmetic in reports.py, then the pages and
the scope of the CSV. Abuse case 7 in docs/security.md."""

import csv
import io
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app as app_module
from app import Employee, db
from reports import headcount_report, last_day_of_month, month_starts
from tests.test_audit import entries

TODAY = date(2026, 11, 11)


def person(start=None, end=None, department=None):
    return SimpleNamespace(
        start_date=start,
        end_date=end,
        status="terminated" if end else "active",
        department=SimpleNamespace(name=department) if department else None,
    )


@pytest.fixture()
def frozen_today(monkeypatch):
    class FrozenDate(date):
        @classmethod
        def today(cls):
            return cls(TODAY.year, TODAY.month, TODAY.day)

    monkeypatch.setattr(app_module, "date", FrozenDate)


def month(report, label):
    row = next(row for row in report.months if row.label == label)
    return row.starting, row.hires, row.leavers, row.ending


# Calendar helpers


def test_month_starts_cover_the_last_twelve_months_oldest_first():
    starts = month_starts(TODAY, 12)

    assert starts[0] == date(2025, 12, 1) and starts[-1] == date(2026, 11, 1)
    assert len(starts) == 12
    assert month_starts(date(2026, 1, 31), 3) == [date(2025, 11, 1), date(2025, 12, 1), date(2026, 1, 1)]


@pytest.mark.parametrize(
    "first,last",
    [
        (date(2026, 2, 1), date(2026, 2, 28)),
        (date(2028, 2, 1), date(2028, 2, 29)),
        (date(2026, 12, 1), date(2026, 12, 31)),
        (date(2026, 4, 1), date(2026, 4, 30)),
    ],
)
def test_last_day_of_month(first, last):
    assert last_day_of_month(first) == last


# The arithmetic


def test_hires_and_leavers_land_in_their_months():
    people = [
        person(date(2020, 1, 6)),                                    # here throughout
        person(date(2026, 3, 16)),                                   # hired in March
        person(date(2019, 5, 1), date(2026, 6, 30)),                 # last day at the end of June
        person(None),                                                # no start date: here throughout
    ]

    report = headcount_report(people, TODAY)

    assert month(report, "2026-02") == (3, 0, 0, 3)
    assert month(report, "2026-03") == (3, 1, 0, 4)
    assert month(report, "2026-06") == (4, 0, 1, 3)
    assert month(report, "2026-07") == (3, 0, 0, 3)
    assert (report.headcount, report.hires, report.leavers) == (3, 1, 1)


def test_someone_is_on_staff_through_their_last_day():
    first_of_month = headcount_report([person(date(2020, 1, 6), date(2026, 7, 1))], TODAY)
    last_of_month = headcount_report([person(date(2020, 1, 6), date(2026, 6, 30))], TODAY)

    assert month(first_of_month, "2026-07") == (1, 0, 1, 0)   # still counted at the start of July
    assert month(last_of_month, "2026-07") == (0, 0, 0, 0)


def test_hired_and_gone_in_the_same_month_counts_as_both():
    report = headcount_report([person(date(2026, 5, 4), date(2026, 5, 22))], TODAY)

    assert month(report, "2026-05") == (0, 1, 1, 0)


def test_ending_always_equals_starting_plus_hires_minus_leavers_and_chains():
    people = [person(date(2024 + n % 3, 1 + n % 12, 1 + n % 27), date(2026, 1 + n % 11, 15) if n % 4 == 0 else None)
              for n in range(40)]

    report = headcount_report(people, TODAY)

    for earlier, later in zip(report.months, report.months[1:], strict=False):
        assert earlier.ending == later.starting
    assert all(row.ending == row.starting + row.hires - row.leavers for row in report.months)


def test_future_start_dates_and_last_days_do_not_count_yet():
    people = [
        person(date(2020, 1, 6)),
        person(date(2026, 11, 20)),                    # starts later this month
        person(date(2020, 1, 6), date(2026, 11, 30)),  # leaving later this month
        person(date(2027, 1, 4)),                      # starts next year
    ]

    report = headcount_report(people, TODAY)

    assert month(report, "2026-11") == (2, 0, 0, 2)
    assert report.headcount == 2


def test_attrition_is_leavers_over_average_headcount():
    people = [person(date(2020, 1, 6)) for _ in range(9)] + [person(date(2020, 1, 6), date(2026, 5, 29))]

    report = headcount_report(people, TODAY)

    # Ten people for five and a half months, nine for the rest: average 9.46.
    assert report.average_headcount.quantize(Decimal("0.01")) == Decimal("9.46")
    assert report.attrition_rate == Decimal("10.6")


def test_attrition_is_undefined_with_nobody_on_staff():
    assert headcount_report([], TODAY).attrition_rate is None
    assert headcount_report([], TODAY).headcount == 0


def test_leavers_without_a_last_day_are_set_aside_and_counted():
    undated = SimpleNamespace(start_date=date(2020, 1, 6), end_date=None, status="terminated", department=None)

    report = headcount_report([person(date(2020, 1, 6)), undated], TODAY)

    assert report.undated_leavers == 1
    assert report.headcount == 1
    assert report.leavers == 0


def test_last_day_only_counts_for_people_marked_as_left():
    rehired = SimpleNamespace(start_date=date(2020, 1, 6), end_date=date(2026, 3, 1), status="active", department=None)

    assert headcount_report([rehired], TODAY).headcount == 1


def test_departments_show_current_headcount_hires_and_leavers():
    people = [
        person(date(2020, 1, 6), department="Engineering"),
        person(date(2026, 4, 6), department="Engineering"),
        person(date(2019, 1, 7), date(2026, 2, 27), department="Finance"),
        person(date(2018, 1, 8), date(2024, 2, 27), department="Legal"),   # left before the period
        person(date(2021, 1, 4)),
    ]

    report = headcount_report(people, TODAY)

    assert report.departments == [("Engineering", 2, 1, 0), ("No department", 1, 0, 0), ("Finance", 0, 0, 1)]


# Pages and scope


@pytest.fixture()
def history(app, staff, frozen_today):
    """Casey was hired this year, Rowan left this year, everyone else predates the period."""
    with app.app_context():
        for key in staff:
            db.session.get(Employee, staff[key]).start_date = date(2021, 3, 1)
        db.session.get(Employee, 1).start_date = date(2021, 3, 1)
        db.session.get(Employee, staff["casey"]).start_date = date(2026, 4, 6)
        rowan = db.session.get(Employee, staff["rowan"])
        rowan.status, rowan.end_date = "terminated", date(2026, 8, 28)
        db.session.commit()
    return staff


def csv_rows(response):
    return list(csv.DictReader(io.StringIO(response.data.decode())))


def test_hr_report_covers_the_whole_company(history, sign_in):
    hr = sign_in("emerson@peoplebase.test")

    page = hr.get("/reports/headcount").data
    rows = csv_rows(hr.get("/reports/headcount.csv"))

    assert b"Whole company" in page
    assert b"<dt>On staff today</dt><dd class=\"num\">7</dd>" in page
    assert rows[0] == {
        "month": "2025-12", "starting_headcount": "7", "hires": "0", "leavers": "0", "ending_headcount": "7",
    }
    assert [row["ending_headcount"] for row in rows[-4:]] == ["7", "7", "7", "7"]
    assert {row["month"]: row["hires"] for row in rows}["2026-04"] == "1"
    assert {row["month"]: row["leavers"] for row in rows}["2026-08"] == "1"


def test_manager_report_is_cut_to_their_reporting_tree(history, sign_in):
    riley = sign_in("riley@peoplebase.test")   # Casey and Jordan
    harper = sign_in("harper@peoplebase.test")  # Rowan, who left in August

    riley_page, riley_rows = riley.get("/reports/headcount").data, csv_rows(riley.get("/reports/headcount.csv"))
    harper_rows = csv_rows(harper.get("/reports/headcount.csv"))

    assert b"Your reporting tree" in riley_page
    assert b"<dt>On staff today</dt><dd class=\"num\">2</dd>" in riley_page
    assert [riley_rows[0]["starting_headcount"], riley_rows[-1]["ending_headcount"]] == ["1", "2"]
    assert sum(int(row["leavers"]) for row in riley_rows) == 0       # Rowan is not Riley's
    assert sum(int(row["leavers"]) for row in harper_rows) == 1
    assert harper_rows[-1]["ending_headcount"] == "0"


def test_skip_level_manager_sees_indirect_reports_too(history, sign_in):
    rows = csv_rows(sign_in("morgan@peoplebase.test").get("/reports/headcount.csv"))

    assert rows[-1]["ending_headcount"] == "5"   # Riley, Casey, Jordan, Harper, Emerson
    assert sum(int(row["leavers"]) for row in rows) == 1


@pytest.mark.parametrize("path", ["/reports/headcount", "/reports/headcount.csv"])
def test_employees_get_no_report_and_no_csv(history, sign_in, path):
    response = sign_in("casey@peoplebase.test").get(path)

    assert response.status_code == 403
    assert b"starting_headcount" not in response.data
    assert b'href="/reports/headcount"' not in sign_in("casey@peoplebase.test").get("/").data


def test_csv_scope_cannot_be_widened_with_parameters(history, sign_in):
    riley = sign_in("riley@peoplebase.test")
    plain = riley.get("/reports/headcount.csv").data

    for query in ("scope=company", "all=1", "manager_id=1", "role=admin", "employee_id=1"):
        assert riley.get(f"/reports/headcount.csv?{query}").data == plain


def test_report_csv_headers_and_caching(history, sign_in):
    response = sign_in("emerson@peoplebase.test").get("/reports/headcount.csv")

    assert response.headers["Content-Type"] == "text/csv; charset=utf-8"
    assert "peoplebase-headcount.csv" in response.headers["Content-Disposition"]
    assert response.headers["Cache-Control"] == "no-store"
    assert response.data.splitlines()[0] == b"month,starting_headcount,hires,leavers,ending_headcount"
    assert len(response.data.splitlines()) == 13


def test_report_exports_are_audited_with_their_scope(app, history, sign_in):
    sign_in("riley@peoplebase.test").get("/reports/headcount.csv")
    sign_in("emerson@peoplebase.test").get("/reports/headcount.csv")

    riley, hr = entries(app, "export.headcount_csv")
    assert riley["actor"] == "riley@peoplebase.test"
    assert riley["details"] == {"scope": "Your reporting tree", "people": 2}
    assert (hr["actor"], hr["details"]) == ("emerson@peoplebase.test", {"scope": "Whole company", "people": 8})


def test_report_mentions_leavers_without_a_last_day(app, history, sign_in):
    with app.app_context():
        db.session.get(Employee, history["jordan"]).status = "terminated"
        db.session.commit()

    page = sign_in("emerson@peoplebase.test").get("/reports/headcount").data

    assert b"1 person is marked as left with no last day on record and is not counted." in page


def test_report_for_a_manager_with_no_reports_is_empty_not_an_error(app, history, sign_in):
    with app.app_context():
        for key in ("casey", "jordan"):
            db.session.get(Employee, history[key]).manager_id = history["harper"]
        db.session.commit()

    page = sign_in("riley@peoplebase.test").get("/reports/headcount")

    assert page.status_code == 200
    assert b"Nobody in this view yet." in page.data and b"n/a" in page.data
