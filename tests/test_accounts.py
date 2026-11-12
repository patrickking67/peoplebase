"""HR managing accounts and roles. Abuse cases 5 and 10 in docs/security.md."""

import re

import pytest

from app import Account, Employee, db
from tests.conftest import HR_EMAIL, TEST_PASSWORD, csrf_from


def post(client, path, **data):
    return client.post(path, data={"csrf_token": csrf_from(client, "/"), **data})


def account(app, email):
    with app.app_context():
        found = db.session.scalar(db.select(Account).where(Account.email == email))
        if found is not None:
            db.session.expunge(found)
        return found


def issued_password(response):
    return re.search(rb'<code class="secret">([^<]+)</code>', response.data).group(1).decode()


def attempt(app, email, password):
    client = app.test_client()
    return client, client.post("/login", data={"email": email, "password": password, "csrf_token": csrf_from(client)})


def test_accounts_page_lists_accounts(client, staff):
    page = client.get("/accounts").data

    assert b"riley@peoplebase.test" in page
    assert b"This is you" in page
    assert b"Never signed in" in page


def test_hr_creates_an_account_with_a_one_time_temporary_password(app, client):
    response = post(client, "/accounts", email="Avery@PeopleBase.Test", role="employee", employee_id="1")

    assert response.status_code == 201
    password = issued_password(response)
    created = account(app, "avery@peoplebase.test")
    assert created.role == "employee" and created.employee_id == 1
    assert created.must_change_password is True
    assert created.password_hash.startswith("$argon2id$") and password not in created.password_hash
    assert len(password) >= 12
    # Shown once: it is not on the next page load and not in the session cookie.
    assert password.encode() not in client.get("/accounts").data
    assert password not in client.get_cookie("session").value

    avery, signed_in = attempt(app, "avery@peoplebase.test", password)
    assert signed_in.status_code == 302
    assert avery.get("/").headers["Location"] == "/account/password"


@pytest.mark.parametrize(
    "fields,error",
    [
        ({"email": "not-an-email"}, b"valid email"),
        ({"email": "two words@peoplebase.test"}, b"valid email"),
        ({"email": HR_EMAIL}, b"already exists"),
        ({"role": "owner"}, b"valid role"),
        ({"role": ""}, b"valid role"),
        ({"employee_id": ""}, b"must be linked to an employee record"),
        ({"employee_id": "999"}, b"valid employee"),
        ({"employee_id": "1; DROP TABLE account"}, b"valid employee"),
    ],
)
def test_invalid_accounts_are_rejected(app, client, fields, error):
    form = {"email": "avery@peoplebase.test", "role": "employee", "employee_id": "1", **fields}

    response = post(client, "/accounts", **form)

    assert response.status_code == 400
    assert error in response.data
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Account.id))) == 1


def test_one_account_per_employee_and_none_for_people_who_left(app, client, staff):
    taken = post(client, "/accounts", email="second@peoplebase.test", role="employee", employee_id=str(staff["casey"]))
    with app.app_context():
        db.session.get(Employee, 1).status = "terminated"
        db.session.commit()
    left = post(client, "/accounts", email="avery@peoplebase.test", role="employee", employee_id="1")

    assert taken.status_code == 400 and b"already has an account" in taken.data
    assert left.status_code == 400 and b"no longer works here" in left.data


def test_an_admin_account_may_stand_alone(app, client):
    response = post(client, "/accounts", email="second.hr@peoplebase.test", role="admin", employee_id="")

    assert response.status_code == 201
    assert account(app, "second.hr@peoplebase.test").employee_id is None


def test_role_change_takes_effect_and_ends_existing_sessions(app, client, staff, sign_in):
    casey = sign_in("casey@peoplebase.test")
    casey_account = account(app, "casey@peoplebase.test")

    response = post(client, f"/accounts/{casey_account.id}/role", role="admin")

    assert response.status_code == 302
    assert account(app, "casey@peoplebase.test").role == "admin"
    assert casey.get("/").status_code == 302  # the old session is gone
    assert sign_in("casey@peoplebase.test").get("/accounts").status_code == 200


def test_staff_cannot_promote_themselves_or_anyone(app, staff, sign_in):
    casey = sign_in("casey@peoplebase.test")
    own = account(app, "casey@peoplebase.test")

    responses = [
        post(casey, f"/accounts/{own.id}/role", role="admin"),
        post(casey, "/accounts", email="sock@peoplebase.test", role="admin"),
        post(casey, f"/accounts/{own.id}/active", active="1"),
        post(casey, f"/accounts/{account(app, HR_EMAIL).id}/password"),
    ]

    assert [response.status_code for response in responses] == [403, 403, 403, 403]
    assert account(app, "casey@peoplebase.test").role == "employee"
    assert account(app, "sock@peoplebase.test") is None


def test_role_fields_on_other_forms_are_ignored(app, client, staff, sign_in):
    post(
        client,
        f"/employees/{staff['casey']}/edit",
        name="Casey Example", position="Engineer", salary="128000", role="admin", account_role="admin",
    )
    casey = sign_in("casey@peoplebase.test")
    post(
        casey, "/account/password",
        current_password=TEST_PASSWORD, new_password="another long password", confirm_password="another long password",
        role="admin", must_change_password="0",
    )

    assert account(app, "casey@peoplebase.test").role == "employee"


def test_nobody_changes_their_own_role_or_turns_off_their_own_account(app, client):
    own = account(app, HR_EMAIL)

    demoted = post(client, f"/accounts/{own.id}/role", role="employee")
    turned_off = post(client, f"/accounts/{own.id}/active", active="0")
    reset = post(client, f"/accounts/{own.id}/password")

    assert demoted.status_code == turned_off.status_code == reset.status_code == 302
    after = account(app, HR_EMAIL)
    assert after.role == "admin" and after.active is True
    assert after.password_hash == own.password_hash


def test_invalid_role_changes_are_refused(app, client, staff):
    casey = account(app, "casey@peoplebase.test")
    second = post(client, "/accounts", email="second.hr@peoplebase.test", role="admin")
    assert second.status_code == 201
    lone_admin = account(app, "second.hr@peoplebase.test")

    post(client, f"/accounts/{casey.id}/role", role="superuser")
    post(client, f"/accounts/{lone_admin.id}/role", role="employee")

    assert account(app, "casey@peoplebase.test").role == "employee"
    assert account(app, "second.hr@peoplebase.test").role == "admin"  # no employee record to fall back on


def test_turning_an_account_off_and_on(app, client, staff, sign_in):
    casey = sign_in("casey@peoplebase.test")
    casey_account = account(app, "casey@peoplebase.test")

    post(client, f"/accounts/{casey_account.id}/active", active="0")
    assert casey.get("/").status_code == 302
    assert attempt(app, "casey@peoplebase.test", TEST_PASSWORD)[1].status_code == 401

    post(client, f"/accounts/{casey_account.id}/active", active="1")
    assert attempt(app, "casey@peoplebase.test", TEST_PASSWORD)[1].status_code == 302


def test_account_of_someone_who_left_cannot_be_turned_back_on(app, client, staff):
    post(
        client, f"/employees/{staff['casey']}/edit",
        name="Casey Example", position="Engineer", salary="128000", status="terminated",
    )
    casey_account = account(app, "casey@peoplebase.test")

    response = post(client, f"/accounts/{casey_account.id}/active", active="1")

    assert response.status_code == 302
    assert account(app, "casey@peoplebase.test").active is False


def test_password_reset_issues_a_temporary_password_and_ends_sessions(app, client, staff, sign_in):
    casey = sign_in("casey@peoplebase.test")
    casey_account = account(app, "casey@peoplebase.test")

    response = post(client, f"/accounts/{casey_account.id}/password")

    password = issued_password(response)
    assert casey.get("/").status_code == 302
    assert attempt(app, "casey@peoplebase.test", TEST_PASSWORD)[1].status_code == 401
    fresh, signed_in = attempt(app, "casey@peoplebase.test", password)
    assert signed_in.status_code == 302
    assert fresh.get("/").headers["Location"] == "/account/password"


def test_password_reset_lifts_a_lockout(app, client, staff):
    for _ in range(5):
        attempt(app, "casey@peoplebase.test", "not the password")
    assert attempt(app, "casey@peoplebase.test", TEST_PASSWORD)[1].status_code == 429
    assert b"Locked" in client.get("/accounts").data

    password = issued_password(post(client, f"/accounts/{account(app, 'casey@peoplebase.test').id}/password"))

    assert attempt(app, "casey@peoplebase.test", password)[1].status_code == 302


def test_account_actions_need_csrf_and_post(app, client, staff):
    casey_account = account(app, "casey@peoplebase.test")

    for action in ("role", "active", "password"):
        assert client.post(f"/accounts/{casey_account.id}/{action}", data={"role": "admin"}).status_code == 400
        assert client.get(f"/accounts/{casey_account.id}/{action}").status_code == 405
    assert client.post("/accounts/999/role", data={"csrf_token": csrf_from(client, "/")}).status_code == 404
    assert account(app, "casey@peoplebase.test").role == "employee"
