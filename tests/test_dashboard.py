from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app import (
    Department,
    Employee,
    TimeOffRequest,
    anniversary_on,
    build_org_tree,
    db,
    headcount_by_department,
    upcoming_anniversaries,
)


def person(id, name, start_date=None, manager_id=None, department=None):
    return SimpleNamespace(
        id=id, name=name, start_date=start_date, manager_id=manager_id, department=department
    )


# Anniversaries


def test_anniversary_on_leap_day_falls_back_to_february_28():
    assert anniversary_on(date(2024, 2, 29), 2025) == date(2025, 2, 28)
    assert anniversary_on(date(2024, 2, 29), 2028) == date(2028, 2, 29)


def test_upcoming_anniversaries_window_and_order():
    today = date(2026, 11, 7)
    people = [
        person(1, "Later Example", date(2020, 12, 1)),
        person(2, "Today Example", date(2023, 11, 7)),
        person(3, "Past Example", date(2021, 11, 6)),
        person(4, "Too Far Example", date(2019, 12, 8)),
        person(5, "New Hire Example", date(2026, 11, 20)),
        person(6, "No Date Example"),
    ]

    result = upcoming_anniversaries(people, today)

    assert [(item["employee"].name, item["years"]) for item in result] == [
        ("Today Example", 3),
        ("Later Example", 6),
    ]


def test_upcoming_anniversaries_cross_the_new_year():
    result = upcoming_anniversaries([person(1, "January Example", date(2020, 1, 3))], date(2026, 12, 20))

    assert result[0]["date"] == date(2027, 1, 3)
    assert result[0]["years"] == 7


# Org tree


def test_org_tree_nests_reports_under_managers():
    people = [
        person(1, "Root Example"),
        person(2, "Middle Example", manager_id=1),
        person(3, "Leaf Example", manager_id=2),
        person(4, "Orphan Example", manager_id=99),
    ]

    tree = build_org_tree(people)

    assert [node["employee"].name for node in tree] == ["Root Example", "Orphan Example"]
    assert tree[0]["reports"][0]["employee"].name == "Middle Example"
    assert tree[0]["reports"][0]["reports"][0]["employee"].name == "Leaf Example"


def test_org_tree_survives_a_reporting_loop():
    people = [person(1, "Loop A", manager_id=2), person(2, "Loop B", manager_id=1)]

    tree = build_org_tree(people)

    names = []

    def walk(nodes):
        for node in nodes:
            names.append(node["employee"].name)
            walk(node["reports"])

    walk(tree)
    assert sorted(names) == ["Loop A", "Loop B"]


def test_headcount_by_department_sorts_largest_first():
    ops = SimpleNamespace(name="Operations")
    design = SimpleNamespace(name="Design")
    people = [
        person(1, "A", department=design),
        person(2, "B", department=ops),
        person(3, "C", department=ops),
        person(4, "D"),
    ]

    assert headcount_by_department(people) == [("Operations", 2), ("Design", 1), ("No department", 1)]


# Pages


@pytest.fixture()
def staffed(app):
    today = date.today()
    with app.app_context():
        engineering = Department(name="Engineering")
        lead = Employee(
            name="Morgan Example",
            position="Director",
            salary=1,
            department=engineering,
            start_date=anniversary_on(today, today.year - 4),
        )
        report = Employee(name="Riley Example", position="Lead", salary=1, department=engineering, manager=lead)
        leaver = Employee(name="Former Example", position="Clerk", salary=1, status="terminated")
        away = Employee(name="Sam Example", position="Analyst", salary=1, status="on_leave")
        db.session.add_all([engineering, lead, report, leaver, away])
        db.session.flush()
        yesterday, tomorrow = today - timedelta(days=1), today + timedelta(days=1)
        next_week = today + timedelta(days=9)
        db.session.add_all(
            [
                TimeOffRequest(
                    employee=report, kind="vacation", start_date=yesterday, end_date=tomorrow, status="approved"
                ),
                TimeOffRequest(employee=lead, kind="personal", start_date=next_week, end_date=next_week),
            ]
        )
        db.session.commit()


def test_dashboard_summarizes_the_organization(client, staffed):
    response = client.get("/")

    assert response.status_code == 200
    assert b"3 people at work across 1 department." in response.data
    assert b"1 out today." in response.data
    assert b"1 on leave." in response.data
    assert b"1 time-off request waiting" in response.data
    assert b"Engineering" in response.data
    assert b"Morgan Example" in response.data  # anniversary today
    assert b"4 years on" in response.data
    assert b"Riley Example" in response.data  # out today


def test_dashboard_excludes_people_who_have_left_from_headcount(client, staffed):
    response = client.get("/")

    assert b"Former Example" not in response.data


def test_empty_dashboard(app):
    with app.app_context():
        db.session.delete(db.session.get(Employee, 1))
        db.session.commit()

    response = app.test_client().get("/")

    assert response.status_code == 200
    assert b"0 people at work." in response.data
    assert b"No time off waiting for a decision." in response.data
    assert b"Everyone is in today." in response.data


def test_org_chart_shows_reporting_lines(client, staffed):
    response = client.get("/org")

    assert response.status_code == 200
    body = response.data
    assert body.index(b"Morgan Example") < body.index(b"Riley Example")
    assert b"Former Example" not in body
    assert b"On leave" in body


# Demo data


def test_seed_demo_fills_an_empty_database(app):
    with app.app_context():
        db.session.delete(db.session.get(Employee, 1))
        db.session.commit()

    result = app.test_cli_runner().invoke(args=["seed-demo"])

    assert result.exit_code == 0, result.output
    assert "Added 13 demo employees in 5 departments and 5 time-off requests." in result.output
    with app.app_context():
        names = db.session.scalars(db.select(Employee.name)).all()
        assert all(name.endswith(" Example") for name in names)
        jordan = db.session.scalar(db.select(Employee).where(Employee.name == "Jordan Example"))
        assert [change.reason for change in jordan.compensation_history] == ["Promotion", "Hired"]
        assert jordan.manager.name == "Riley Example"

    client = app.test_client()
    for path in ["/", "/employees", "/org", "/departments", "/time-off"]:
        assert client.get(path).status_code == 200


def test_seed_demo_refuses_a_database_with_people(app):
    result = app.test_cli_runner().invoke(args=["seed-demo"])

    assert result.exit_code != 0
    assert "already has employees" in result.output
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Employee.id))) == 1
