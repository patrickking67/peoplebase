"""Who can see and change what. Abuse cases 1, 2, 5, 6, 7 and 15 in
docs/security.md, each tried for real against the server."""

import re
from datetime import date

import pytest

from access import Access, reporting_tree
from app import Account, Employee, TimeOffRequest, db
from tests.conftest import HR_EMAIL, csrf_from


def post(client, path, **data):
    return client.post(path, data={"csrf_token": csrf_from(client, "/"), **data})


def email(name):
    return f"{name}@peoplebase.test"


def add_request(app, employee_id, status="pending", start=date(2026, 12, 21), end=date(2026, 12, 24)):
    with app.app_context():
        item = TimeOffRequest(employee_id=employee_id, kind="vacation", start_date=start, end_date=end, status=status)
        db.session.add(item)
        db.session.commit()
        return item.id


def request_status(app, request_id):
    with app.app_context():
        return db.session.get(TimeOffRequest, request_id).status


# Every route declares who may use it.
# endpoint: (method, path, anonymous, employee, manager, admin). Employee is
# Casey and manager is Riley, Casey's manager. None means "covered elsewhere".
ROUTES = {
    "static": ("GET", "/static/styles.css", 200, 200, 200, 200),
    "login": ("GET", "/login", 200, 302, 302, 302),
    "logout": ("POST", "/logout", 302, None, None, None),
    "change_password": ("GET", "/account/password", 302, 200, 200, 200),
    "home": ("GET", "/", 302, 200, 200, 200),
    "org_chart": ("GET", "/org", 302, 200, 200, 200),
    "view_employees": ("GET", "/employees", 302, 200, 200, 200),
    "export_employees": ("GET", "/employees.csv", 302, 403, 403, 200),
    "add_employee": ("GET", "/employees/new", 302, 403, 403, 200),
    "employee_profile": ("GET", "/employees/{casey}", 302, 200, 200, 200),
    "update_employee": ("GET", "/employees/{casey}/edit", 302, 403, 403, 200),
    "delete_employee": ("POST", "/employees/{rowan}/delete", 302, 403, 403, 302),
    "view_departments": ("GET", "/departments", 302, 403, 403, 200),
    "add_department": ("POST", "/departments", 302, 403, 403, 400),
    "rename_department": ("POST", "/departments/1/rename", 302, 403, 403, 400),
    "delete_department": ("POST", "/departments/1/delete", 302, 403, 403, 302),
    "view_time_off": ("GET", "/time-off", 302, 200, 200, 200),
    "request_time_off": ("GET", "/time-off/new", 302, 200, 200, 200),
    "decide_time_off": ("POST", "/time-off/999/decide", 302, 404, 404, 404),
    "view_accounts": ("GET", "/accounts", 302, 403, 403, 200),
    "add_account": ("POST", "/accounts", 302, 403, 403, 400),
    "change_account_role": ("POST", "/accounts/1/role", 302, 403, 403, 302),
    "set_account_active": ("POST", "/accounts/1/active", 302, 403, 403, 302),
    "reset_account_password": ("POST", "/accounts/1/password", 302, 403, 403, 302),
    "view_audit": ("GET", "/audit", 302, 403, 403, 200),
    "view_checklists": ("GET", "/checklists", 302, 403, 200, 200),
    "view_headcount_report": ("GET", "/reports/headcount", 302, 403, 200, 200),
    "export_headcount_report": ("GET", "/reports/headcount.csv", 302, 403, 200, 200),
    "view_policies": ("GET", "/policies", 302, 200, 200, 200),
    "add_policy": ("GET", "/policies/new", 302, 403, 403, 200),
    "view_policy": ("GET", "/policies/999", 302, 404, 404, 404),
    "acknowledge_policy": ("POST", "/policies/999/acknowledge", 302, 404, 404, 404),
    "revise_policy": ("POST", "/policies/999/revise", 302, 403, 403, 404),
    "retire_policy": ("POST", "/policies/999/retire", 302, 403, 403, 404),
    "update_checklist_task": ("POST", "/checklists/999", 302, 404, 404, 404),
}


def test_every_route_has_a_declared_access_rule(app):
    endpoints = {rule.endpoint for rule in app.url_map.iter_rules() if not rule.endpoint.startswith("db.")}

    assert endpoints == set(ROUTES), "add new routes to ROUTES with the status each role should get"


@pytest.mark.parametrize("endpoint", sorted(ROUTES))
def test_route_access_by_role(app, staff, sign_in, anonymous, endpoint):
    method, path, *expected = ROUTES[endpoint]
    path = path.format(**staff)
    clients = [anonymous, sign_in(email("casey")), sign_in(email("riley")), sign_in(HR_EMAIL)]

    for role, client, status in zip(["anonymous", "employee", "manager", "admin"], clients, expected, strict=True):
        if status is None:
            continue
        if method == "GET":
            response = client.get(path)
        else:
            response = client.post(path, data={"csrf_token": csrf_from(client, "/")})
        assert response.status_code == status, f"{role} {method} {path}"
        if role == "anonymous" and status == 302:
            assert response.headers["Location"].startswith("/login")


# Profiles (IDOR on employee ids)


@pytest.mark.parametrize(
    "viewer,target,status",
    [
        ("casey", "casey", 200),      # own record
        ("casey", "jordan", 404),     # a peer
        ("casey", "riley", 404),      # their own manager
        ("riley", "casey", 200),      # direct report
        ("riley", "jordan", 200),
        ("riley", "rowan", 404),      # someone else's report
        ("riley", "morgan", 404),     # their own manager
        ("morgan", "casey", 200),     # indirect report
        ("morgan", "rowan", 200),
        ("harper", "casey", 404),     # a manager, but not of Casey
        ("emerson", "casey", 200),    # HR
        ("emerson", "morgan", 200),
    ],
)
def test_profile_access_follows_the_reporting_line(staff, sign_in, viewer, target, status):
    assert sign_in(email(viewer)).get(f"/employees/{staff[target]}").status_code == status


def test_out_of_reach_profile_looks_exactly_like_a_missing_one(staff, sign_in):
    casey = sign_in(email("casey"))

    hidden = casey.get(f"/employees/{staff['jordan']}")
    missing = casey.get("/employees/99999")

    assert hidden.status_code == missing.status_code == 404
    assert hidden.data == missing.data
    assert b"Jordan" not in hidden.data


def test_walking_ids_reveals_only_your_own_record(staff, sign_in):
    casey = sign_in(email("casey"))

    opened = [number for number in range(1, 12) if casey.get(f"/employees/{number}").status_code == 200]

    assert opened == [staff["casey"]]


# Pay


def test_salary_is_shown_to_the_employee_and_hr_only(staff, sign_in):
    path = f"/employees/{staff['casey']}"

    own = sign_in(email("casey")).get(path).data
    manager = sign_in(email("riley")).get(path).data
    skip_level = sign_in(email("morgan")).get(path).data
    hr = sign_in(email("emerson")).get(path).data

    for page in (own, hr):
        assert b"$128,000.00" in page
        assert b"Compensation history" in page
    for page in (manager, skip_level):
        assert b"Casey Example" in page
        assert b"128,000" not in page
        assert b"Annual salary" not in page
        assert b"Compensation history" not in page


def test_only_hr_sees_edit_and_delete_controls(staff, sign_in):
    path = f"/employees/{staff['casey']}"

    assert b"Edit record" in sign_in(email("emerson")).get(path).data
    for viewer in ("casey", "riley"):
        page = sign_in(email(viewer)).get(path).data
        assert b"Edit record" not in page
        assert b"/delete" not in page


# Directory and org chart


def test_directory_for_staff_is_a_phone_book_without_pay_or_status(app, staff, sign_in):
    with app.app_context():
        db.session.get(Employee, staff["rowan"]).status = "terminated"
        db.session.get(Employee, staff["jordan"]).status = "on_leave"
        db.session.commit()

    page = sign_in(email("casey")).get("/employees").data

    assert b"Morgan Example" in page and b"Jordan Example" in page
    assert b"Rowan Example" not in page          # people who left
    assert b"$" not in page                       # no salary, no payroll totals
    assert b"Annual payroll" not in page
    assert b"On leave" not in page
    assert b"employees.csv" not in page
    assert b'name="status"' not in page
    assert f'href="/employees/{staff["casey"]}"'.encode() in page
    assert f'href="/employees/{staff["jordan"]}"'.encode() not in page


@pytest.mark.parametrize("query", ["status=terminated", "status=on_leave", "q=Rowan", "position=Accountant"])
def test_staff_cannot_filter_their_way_to_people_who_left(app, staff, sign_in, query):
    with app.app_context():
        db.session.get(Employee, staff["rowan"]).status = "terminated"
        db.session.commit()

    page = sign_in(email("riley")).get(f"/employees?{query}").data

    assert b"Rowan Example" not in page
    assert b"Accountant" not in page


def test_directory_for_hr_still_shows_everything(app, staff, sign_in):
    with app.app_context():
        db.session.get(Employee, staff["rowan"]).status = "terminated"
        db.session.commit()

    page = sign_in(email("emerson")).get("/employees?status=terminated").data

    assert b"Rowan Example" in page
    assert b"$86,000.00" in page
    assert b"employees.csv" in page


def test_org_chart_shows_everyone_but_links_and_status_follow_reach(app, staff, sign_in):
    with app.app_context():
        db.session.get(Employee, staff["jordan"]).status = "on_leave"
        db.session.get(Employee, staff["rowan"]).status = "on_leave"
        db.session.commit()

    casey = sign_in(email("casey")).get("/org").data
    riley = sign_in(email("riley")).get("/org").data

    assert b"Morgan Example" in casey and b"Rowan Example" in casey
    assert b"On leave" not in casey
    assert casey.count(b'<a class="org-node"') == 1
    assert riley.count(b"On leave") == 1          # Jordan reports to Riley, Rowan does not
    assert riley.count(b'<a class="org-node"') == 3


# CSV export scope


@pytest.mark.parametrize("who", ["casey", "riley", "morgan"])
def test_directory_csv_is_refused_to_everyone_but_hr(staff, sign_in, who):
    response = sign_in(email(who)).get("/employees.csv")

    assert response.status_code == 403
    assert b"annual_salary" not in response.data
    assert b"128000" not in response.data


def test_directory_csv_for_hr_has_every_row(staff, sign_in):
    response = sign_in(email("emerson")).get("/employees.csv")

    assert response.status_code == 200
    assert response.data.count(b"Example") == 8  # seven staff plus the seeded Avery


# Changing records


@pytest.mark.parametrize("who", ["casey", "riley", "morgan"])
def test_only_hr_changes_records(app, staff, sign_in, who):
    client = sign_in(email(who))
    form = {"name": "Changed Example", "position": "Chief Everything", "salary": "999999"}

    assert post(client, f"/employees/{staff['casey']}/edit", **form).status_code == 403
    assert post(client, f"/employees/{staff[who]}/edit", **form).status_code == 403
    assert post(client, "/employees/new", **form).status_code == 403
    assert post(client, f"/employees/{staff['casey']}/delete").status_code == 403
    assert post(client, "/departments", name="Shadow IT").status_code == 403

    with app.app_context():
        casey = db.session.get(Employee, staff["casey"])
        assert (casey.name, casey.position, casey.salary) == ("Casey Example", "Engineer", 128000)
        assert db.session.scalar(db.select(db.func.count(Employee.id))) == 8


# Time off


def visible_names(client):
    """First names of the people whose requests are listed."""
    page = client.get("/time-off").data.decode()
    return set(re.findall(r'class="request-who"><a [^>]*>(\w+) Example', page))


def test_time_off_list_is_scoped_to_reach(app, staff, sign_in):
    for name in ("casey", "jordan", "riley", "rowan"):
        add_request(app, staff[name])

    assert visible_names(sign_in(email("casey"))) == {"Casey"}
    assert visible_names(sign_in(email("riley"))) == {"Casey", "Jordan", "Riley"}
    assert visible_names(sign_in(email("harper"))) == {"Rowan"}
    assert visible_names(sign_in(email("morgan"))) == {"Casey", "Jordan", "Riley", "Rowan"}
    assert visible_names(sign_in(email("emerson"))) == {"Casey", "Jordan", "Riley", "Rowan"}


@pytest.mark.parametrize(
    "decider,requester,outcome",
    [
        ("riley", "casey", "approved"),     # direct manager
        ("morgan", "casey", "approved"),    # manager's manager
        ("emerson", "casey", "approved"),   # HR
        ("morgan", "riley", "approved"),
        ("emerson", "morgan", "approved"),  # nobody above Morgan, so HR decides
        ("casey", "casey", "pending"),      # own request
        ("riley", "riley", "pending"),      # a manager's own request
        ("emerson", "emerson", "pending"),  # HR's own request
        ("jordan", "casey", "pending"),     # a peer
        ("harper", "casey", "pending"),     # another team's manager
        ("casey", "riley", "pending"),      # upwards
    ],
)
def test_who_can_decide_a_request(app, staff, sign_in, decider, requester, outcome):
    request_id = add_request(app, staff[requester])

    post(sign_in(email(decider)), f"/time-off/{request_id}/decide", decision="approve")

    assert request_status(app, request_id) == outcome


def test_deciding_out_of_reach_requests_is_a_404_not_a_hint(app, staff, sign_in):
    request_id = add_request(app, staff["casey"])

    response = post(sign_in(email("harper")), f"/time-off/{request_id}/decide", decision="approve")

    assert response.status_code == 404


def test_naming_an_approver_in_the_form_changes_nothing(app, staff, sign_in):
    request_id = add_request(app, staff["casey"])

    response = post(
        sign_in(email("casey")),
        f"/time-off/{request_id}/decide",
        decision="approve", approver_id=str(staff["riley"]), decided_by_id=str(staff["riley"]),
    )

    assert response.status_code == 302
    assert request_status(app, request_id) == "pending"
    with app.app_context():
        assert db.session.get(TimeOffRequest, request_id).decided_by_id is None


def test_staff_request_time_off_only_for_themselves(app, staff, sign_in):
    response = post(
        sign_in(email("casey")),
        "/time-off/new",
        employee_id=str(staff["jordan"]), kind="vacation", start_date="2026-12-21", end_date="2026-12-22",
        status="approved", decided_by_id=str(staff["riley"]),
    )

    assert response.status_code == 302
    with app.app_context():
        item = db.session.scalar(db.select(TimeOffRequest))
        assert item.employee_id == staff["casey"]
        assert item.status == "pending"
        assert item.decided_by_id is None


def test_managers_also_request_only_for_themselves(app, staff, sign_in):
    post(
        sign_in(email("riley")),
        "/time-off/new",
        employee_id=str(staff["casey"]), kind="sick", start_date="2026-12-21", end_date="2026-12-21",
    )

    with app.app_context():
        assert db.session.scalar(db.select(TimeOffRequest)).employee_id == staff["riley"]


def test_request_form_offers_a_person_picker_to_hr_only(staff, sign_in):
    assert b'name="employee_id"' in sign_in(email("emerson")).get("/time-off/new").data
    assert b'name="employee_id"' not in sign_in(email("riley")).get("/time-off/new").data


def test_account_without_an_employee_record_cannot_request(app, staff, sign_in):
    with app.app_context():
        account = db.session.scalar(db.select(Account).where(Account.email == email("casey")))
        account.employee = None
        db.session.commit()

    response = post(
        sign_in(email("casey")), "/time-off/new", kind="vacation", start_date="2026-12-21", end_date="2026-12-22"
    )

    assert response.status_code == 400
    assert b"not linked to an employee record" in response.data


# Dashboards


def test_staff_dashboard_shows_only_their_own_and_their_teams_data(app, staff, sign_in):
    add_request(app, staff["casey"])
    add_request(app, staff["rowan"])

    casey = sign_in(email("casey")).get("/").data
    riley = sign_in(email("riley")).get("/").data
    hr = sign_in(email("emerson")).get("/").data

    assert b"Hello, Casey." in casey
    assert b"Rowan" not in casey and b"Headcount by department" not in casey
    assert b"1 time-off request is waiting on you." in riley
    assert b"Casey Example" in riley and b"Rowan" not in riley
    assert b"Headcount by department" in hr


def test_navigation_matches_the_role(staff, sign_in):
    casey = sign_in(email("casey")).get("/").data
    hr = sign_in(email("emerson")).get("/").data

    assert b'href="/accounts"' not in casey and b'href="/departments"' not in casey
    assert b'href="/accounts"' in hr and b'href="/departments"' in hr


# The policy module on its own


def test_reach_changes_as_soon_as_the_reporting_line_does(app, staff, sign_in):
    riley = sign_in(email("riley"))
    assert riley.get(f"/employees/{staff['casey']}").status_code == 200

    with app.app_context():
        db.session.get(Employee, staff["casey"]).manager_id = staff["harper"]
        db.session.commit()

    assert riley.get(f"/employees/{staff['casey']}").status_code == 404
    assert sign_in(email("harper")).get(f"/employees/{staff['casey']}").status_code == 200


def test_having_reports_is_not_enough_without_the_manager_role(app, staff, sign_in):
    with app.app_context():
        db.session.get(Employee, staff["jordan"]).manager_id = staff["casey"]
        db.session.commit()

    assert sign_in(email("casey")).get(f"/employees/{staff['jordan']}").status_code == 404


def test_reporting_tree_is_direct_and_indirect_reports(app, staff):
    with app.app_context():
        assert reporting_tree(staff["riley"]) == {staff["casey"], staff["jordan"]}
        assert reporting_tree(staff["morgan"]) == set(staff.values()) - {staff["morgan"]}
        assert reporting_tree(staff["casey"]) == frozenset()
        assert reporting_tree(None) == frozenset()


def test_reporting_tree_survives_a_loop_in_the_data(app, staff):
    with app.app_context():
        db.session.get(Employee, staff["morgan"]).manager_id = staff["casey"]
        db.session.commit()

        tree = reporting_tree(staff["riley"])

        assert staff["riley"] not in tree
        assert {staff["casey"], staff["jordan"], staff["morgan"]} <= tree


def test_signed_out_access_object_allows_nothing(app, staff):
    with app.app_context():
        nobody = Access(None)
        casey = db.session.get(Employee, staff["casey"])

        assert not nobody.is_admin
        assert not nobody.can_view_profile(casey)
        assert not nobody.can_view_pay(casey)
        assert not nobody.can_manage_people()
        assert nobody.visible_profile_ids() == frozenset()
