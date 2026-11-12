"""Sign-in, sessions, lockout and passwords. Abuse cases 3, 4, 8, 9, 10, 11, 12
and 14 in docs/security.md."""

from datetime import timedelta

import pytest
from argon2 import PasswordHasher

import app as app_module
from app import Account, Employee, LoginThrottle, create_app, db, hash_password, safe_next
from tests.conftest import FAST_HASHING, HR_EMAIL, TEST_PASSWORD, csrf_from, sign_in_client

NEW_PASSWORD = "four unrelated plain words"


def attempt(client, email, password=TEST_PASSWORD, **extra):
    return client.post(
        "/login", data={"email": email, "password": password, "csrf_token": csrf_from(client), **extra}
    )


def fail(client, email, times, password="not the password"):
    for _ in range(times):
        assert attempt(client, email, password).status_code == 401


def post(client, path, **data):
    return client.post(path, data={"csrf_token": csrf_from(client, "/"), **data})


def account_for(app, email):
    with app.app_context():
        account = db.session.scalar(db.select(Account).where(Account.email == email))
        db.session.expunge(account)
        return account


def shift_clock(monkeypatch, **delta):
    """Move the app's idea of now forward, for lockout windows."""
    real = app_module.utcnow
    monkeypatch.setattr(app_module, "utcnow", lambda: real() + timedelta(**delta))


# Signing in


@pytest.mark.parametrize(
    "path", ["/", "/employees", "/employees/1", "/org", "/time-off", "/accounts", "/employees.csv"]
)
def test_pages_send_anonymous_visitors_to_sign_in(anonymous, path):
    response = anonymous.get(path)

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login?next=")
    assert b"Avery Example" not in response.data


def test_sign_in_page_renders(anonymous):
    response = anonymous.get("/login")

    assert response.status_code == 200
    assert b'autocomplete="current-password"' in response.data
    assert b'class="site-nav"' not in response.data


def test_sign_in_with_the_right_password(app, anonymous):
    response = attempt(anonymous, HR_EMAIL)

    assert response.status_code == 302
    assert response.headers["Location"] == "/"
    assert anonymous.get("/employees").status_code == 200
    assert account_for(app, HR_EMAIL).last_login_at is not None


def test_email_is_matched_ignoring_case_and_spaces(anonymous):
    assert attempt(anonymous, "  HR@PeopleBase.Test ").status_code == 302


def test_signed_in_visitors_skip_the_sign_in_page(client):
    assert client.get("/login").headers["Location"] == "/"


def test_wrong_password_and_unknown_email_look_the_same(app):
    wrong = attempt(app.test_client(), HR_EMAIL, "not the password")
    unknown = attempt(app.test_client(), "nobody@peoplebase.test", "not the password")

    assert wrong.status_code == unknown.status_code == 401
    assert b"Email or password is incorrect." in wrong.data
    assert b"Email or password is incorrect." in unknown.data
    assert wrong.data.count(b"incorrect") == unknown.data.count(b"incorrect")


def test_unknown_email_still_costs_one_hash_verification(app, anonymous, monkeypatch):
    calls = []
    hasher = app.extensions["password_hasher"]
    real_verify = hasher.verify
    monkeypatch.setattr(
        type(hasher), "verify", lambda self, stored, password: calls.append(stored) or real_verify(stored, password)
    )

    attempt(anonymous, "nobody@peoplebase.test", "whatever password")

    assert calls == [app.extensions["password_decoy"]]


@pytest.mark.parametrize("password", ["", "x" * 129, "x" * 5000])
def test_empty_and_oversized_passwords_fail_cleanly(anonymous, password):
    assert attempt(anonymous, HR_EMAIL, password).status_code == 401


def test_sign_in_requires_a_csrf_token(app, anonymous):
    response = anonymous.post("/login", data={"email": HR_EMAIL, "password": TEST_PASSWORD})

    assert response.status_code == 400
    assert anonymous.get("/employees").status_code == 302


def test_posting_without_a_session_changes_nothing(app, anonymous):
    token = csrf_from(anonymous)

    response = anonymous.post("/employees/1/delete", data={"csrf_token": token})

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login")
    with app.app_context():
        assert db.session.get(Employee, 1) is not None


# Where sign-in sends you


@pytest.mark.parametrize(
    "target,expected",
    [
        ("/employees?q=avery", "/employees?q=avery"),
        ("/time-off", "/time-off"),
        ("https://evil.example/", "/"),
        ("//evil.example/", "/"),
        ("/\\evil.example", "/"),
        ("javascript:alert(1)", "/"),
        ("employees", "/"),
        ("", "/"),
    ],
)
def test_next_is_followed_only_inside_the_site(anonymous, target, expected):
    response = attempt(anonymous, HR_EMAIL, next=target)

    assert response.headers["Location"] == expected


def test_safe_next_rejects_control_characters():
    assert safe_next("/employees\r\nSet-Cookie: x=1") is None
    assert safe_next("/employees") == "/employees"


# Lockout


def test_five_failures_lock_the_email_even_for_the_right_password(app, anonymous):
    fail(anonymous, HR_EMAIL, 5)

    locked = attempt(anonymous, HR_EMAIL)

    assert locked.status_code == 429
    assert b"Too many failed sign-ins" in locked.data
    assert anonymous.get("/employees").status_code == 302


def test_four_failures_do_not_lock(anonymous):
    fail(anonymous, HR_EMAIL, 4)

    assert attempt(anonymous, HR_EMAIL).status_code == 302


def test_unknown_emails_lock_the_same_way(anonymous):
    fail(anonymous, "nobody@peoplebase.test", 5)

    assert attempt(anonymous, "nobody@peoplebase.test").status_code == 429


def test_lock_is_per_email(anonymous):
    fail(anonymous, "nobody@peoplebase.test", 5)

    assert attempt(anonymous, HR_EMAIL).status_code == 302


def test_lock_lifts_after_fifteen_minutes(anonymous, monkeypatch):
    fail(anonymous, HR_EMAIL, 5)

    shift_clock(monkeypatch, minutes=14)
    assert attempt(anonymous, HR_EMAIL).status_code == 429
    shift_clock(monkeypatch, minutes=16)
    assert attempt(anonymous, HR_EMAIL).status_code == 302


def test_failures_older_than_the_window_are_forgotten(anonymous, monkeypatch):
    fail(anonymous, HR_EMAIL, 4)
    shift_clock(monkeypatch, minutes=16)
    fail(anonymous, HR_EMAIL, 4)

    assert attempt(anonymous, HR_EMAIL).status_code == 302


def test_successful_sign_in_resets_the_count(app, anonymous):
    fail(anonymous, HR_EMAIL, 4)
    assert attempt(anonymous, HR_EMAIL).status_code == 302
    fresh = app.test_client()
    fail(fresh, HR_EMAIL, 4)

    assert attempt(fresh, HR_EMAIL).status_code == 302


def test_twenty_failures_from_one_address_lock_that_address(app, anonymous):
    # Four tries each against five emails: no single email reaches its limit.
    for number in range(5):
        fail(anonymous, f"guess{number}@peoplebase.test", 4)

    assert attempt(anonymous, HR_EMAIL).status_code == 429
    elsewhere = app.test_client()
    response = elsewhere.post(
        "/login",
        data={"email": HR_EMAIL, "password": TEST_PASSWORD, "csrf_token": csrf_from(elsewhere)},
        environ_base={"REMOTE_ADDR": "203.0.113.9"},
    )
    assert response.status_code == 302


def test_old_throttle_rows_are_pruned(app, anonymous, monkeypatch):
    fail(anonymous, "old@peoplebase.test", 1)
    shift_clock(monkeypatch, days=2)
    fail(anonymous, "new@peoplebase.test", 1)

    with app.app_context():
        keys = set(db.session.scalars(db.select(LoginThrottle.key)))
    assert "email:old@peoplebase.test" not in keys
    assert "email:new@peoplebase.test" in keys


# Sessions


def test_sign_in_discards_whatever_the_session_held_before(anonymous):
    planted_token = csrf_from(anonymous)
    with anonymous.session_transaction() as planted:
        planted["planted"] = "by an attacker"

    assert anonymous.post(
        "/login", data={"email": HR_EMAIL, "password": TEST_PASSWORD, "csrf_token": planted_token}
    ).status_code == 302

    with anonymous.session_transaction() as current:
        assert "planted" not in current
        assert current["account_id"]
    assert csrf_from(anonymous, "/") != planted_token
    # The token an attacker could have known before sign-in no longer works.
    assert anonymous.post("/logout", data={"csrf_token": planted_token}).status_code == 400


def test_sign_out_needs_post_and_a_token(client):
    assert client.get("/logout").status_code == 405
    assert client.post("/logout").status_code == 400
    assert client.get("/employees").status_code == 200


def test_sign_out_ends_the_session(client):
    response = post(client, "/logout")

    assert response.headers["Location"] == "/login"
    assert client.get("/employees").status_code == 302


def test_a_cookie_copied_before_sign_out_is_dead_afterwards(app, client):
    stolen = client.get_cookie("session").value
    post(client, "/logout")

    thief = app.test_client()
    thief.set_cookie("session", stolen)

    assert thief.get("/employees").status_code == 302


def test_session_ends_twelve_hours_after_sign_in(client, monkeypatch):
    real = app_module.time.time
    monkeypatch.setattr(app_module.time, "time", lambda: real() + 11 * 3600)
    assert client.get("/employees").status_code == 200

    monkeypatch.setattr(app_module.time, "time", lambda: real() + 12 * 3600 + 5)
    assert client.get("/employees").status_code == 302


def test_forged_session_fields_are_refused(app, client):
    with client.session_transaction() as current:
        current["nonce"] = "guessed"

    assert client.get("/employees").status_code == 302


def test_signed_in_pages_are_never_cached(client, anonymous):
    assert client.get("/employees/1").headers["Cache-Control"] == "no-store"
    assert client.get("/").headers["Cache-Control"] == "no-store"
    assert anonymous.get("/login").headers["Cache-Control"] == "no-store"
    assert "no-store" not in anonymous.get("/static/styles.css").headers.get("Cache-Control", "")


def test_session_cookie_flags(app, anonymous):
    cookie = attempt(anonymous, HR_EMAIL).headers["Set-Cookie"]

    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert "Secure" not in cookie


def test_session_cookie_is_secure_when_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "1")
    monkeypatch.setenv("SECRET_KEY", "test-only-secret")
    secure_app = create_app({"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'secure.db'}"})

    cookie = secure_app.test_client().get("/login").headers["Set-Cookie"]

    assert "Secure" in cookie


# Accounts that should no longer work


def test_account_turned_off_cannot_sign_in_and_loses_its_session(app, staff, sign_in):
    casey = sign_in("casey@peoplebase.test")
    with app.app_context():
        db.session.scalar(db.select(Account).where(Account.email == "casey@peoplebase.test")).active = False
        db.session.commit()

    assert casey.get("/").status_code == 302
    assert attempt(app.test_client(), "casey@peoplebase.test").status_code == 401


def test_marking_someone_as_left_turns_off_their_account(app, client, staff, sign_in):
    casey = sign_in("casey@peoplebase.test")

    response = post(
        client,
        f"/employees/{staff['casey']}/edit",
        name="Casey Example", position="Engineer", salary="128000", status="terminated",
    )

    assert response.status_code == 302
    account = account_for(app, "casey@peoplebase.test")
    assert account.active is False
    assert account.employee_id == staff["casey"]
    assert casey.get("/").status_code == 302
    assert attempt(app.test_client(), "casey@peoplebase.test").status_code == 401


def test_deleting_a_record_turns_off_and_unlinks_the_account(app, client, staff):
    post(client, f"/employees/{staff['rowan']}/delete")

    account = account_for(app, "rowan@peoplebase.test")
    assert account.active is False
    assert account.employee_id is None
    assert attempt(app.test_client(), "rowan@peoplebase.test").status_code == 401


def test_hr_cannot_mark_or_delete_their_own_record(app, staff, sign_in):
    emerson = sign_in("emerson@peoplebase.test")

    marked = post(
        emerson,
        f"/employees/{staff['emerson']}/edit",
        name="Emerson Example", position="People Partner", salary="104000", status="terminated",
    )
    deleted = post(emerson, f"/employees/{staff['emerson']}/delete")

    assert marked.status_code == 400
    assert b"cannot mark your own record" in marked.data
    assert deleted.status_code == 302
    with app.app_context():
        assert db.session.get(Employee, staff["emerson"]).status == "active"
    assert emerson.get("/accounts").status_code == 200


# Passwords


def test_passwords_are_stored_as_argon2id_hashes(app):
    stored = account_for(app, HR_EMAIL).password_hash

    assert stored.startswith("$argon2id$")
    assert TEST_PASSWORD not in stored


def test_default_hash_settings_are_the_library_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "test-only-secret")
    real_app = create_app({"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'real.db'}"})
    hasher, default = real_app.extensions["password_hasher"], PasswordHasher()

    assert (hasher.time_cost, hasher.memory_cost, hasher.parallelism) == (
        default.time_cost, default.memory_cost, default.parallelism
    )
    assert hasher.memory_cost >= 19 * 1024  # OWASP's floor for Argon2id


def test_old_hash_settings_are_upgraded_at_sign_in(app, anonymous):
    weaker = PasswordHasher(time_cost=1, memory_cost=16, parallelism=1).hash(TEST_PASSWORD)
    with app.app_context():
        db.session.scalar(db.select(Account).where(Account.email == HR_EMAIL)).password_hash = weaker
        db.session.commit()

    assert attempt(anonymous, HR_EMAIL).status_code == 302

    upgraded = account_for(app, HR_EMAIL).password_hash
    assert upgraded != weaker
    assert f"m={FAST_HASHING['memory_cost']}," in upgraded
    assert attempt(app.test_client(), HR_EMAIL).status_code == 302


def change(client, current=TEST_PASSWORD, new=NEW_PASSWORD, confirm=None):
    return post(
        client, "/account/password",
        current_password=current, new_password=new, confirm_password=new if confirm is None else confirm,
    )


def test_changing_the_password(app, client):
    response = change(client)

    assert response.status_code == 302
    assert attempt(app.test_client(), HR_EMAIL, TEST_PASSWORD).status_code == 401
    assert attempt(app.test_client(), HR_EMAIL, NEW_PASSWORD).status_code == 302


def test_changing_the_password_signs_out_other_sessions_only(app, client):
    other_browser = sign_in_client(app, HR_EMAIL)

    change(client)

    assert client.get("/employees").status_code == 200
    assert other_browser.get("/employees").status_code == 302


@pytest.mark.parametrize(
    "fields,error",
    [
        ({"current": "not the password"}, b"current password is incorrect"),
        ({"new": "short"}, b"at least 12 characters"),
        ({"new": "x" * 129}, b"at most 128 characters"),
        ({"confirm": "something else entirely"}, b"do not match"),
        ({"new": TEST_PASSWORD}, b"have not just been using"),
        ({"new": HR_EMAIL.upper()}, b"cannot be your email"),
    ],
)
def test_bad_password_changes_are_refused(app, client, fields, error):
    response = change(client, **fields)

    assert response.status_code == 400
    assert error in response.data
    assert attempt(app.test_client(), HR_EMAIL).status_code == 302


def test_temporary_password_must_be_replaced_before_anything_else(app, staff, sign_in):
    with app.app_context():
        casey_account = db.session.scalar(db.select(Account).where(Account.email == "casey@peoplebase.test"))
        casey_account.must_change_password = True
        db.session.commit()
    casey = sign_in("casey@peoplebase.test")

    for path in ["/", "/employees", f"/employees/{staff['casey']}", "/time-off"]:
        assert casey.get(path).headers["Location"] == "/account/password"
    page = casey.get("/account/password")
    assert b"Choose your own password." in page.data
    assert b'class="site-nav"' not in page.data
    blocked = casey.post("/time-off/new", data={"csrf_token": csrf_from(casey, "/account/password")})
    assert blocked.headers["Location"] == "/account/password"

    assert casey.post(
        "/account/password",
        data={
            "csrf_token": csrf_from(casey, "/account/password"),
            "current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD,
        },
    ).status_code == 302
    assert casey.get("/").status_code == 200


# create-admin


def test_create_admin_command(app):
    result = app.test_cli_runner().invoke(
        args=["create-admin", "--email", "First.Admin@PeopleBase.Test", "--password", NEW_PASSWORD]
    )

    assert result.exit_code == 0, result.output
    account = account_for(app, "first.admin@peoplebase.test")
    assert account.role == "admin"
    assert account.must_change_password is False
    assert NEW_PASSWORD not in result.output
    assert attempt(app.test_client(), "first.admin@peoplebase.test", NEW_PASSWORD).status_code == 302


@pytest.mark.parametrize(
    "email,password,error",
    [
        ("not-an-email", NEW_PASSWORD, "valid email"),
        (HR_EMAIL, NEW_PASSWORD, "already exists"),
        ("new@peoplebase.test", "short", "at least 12"),
    ],
)
def test_create_admin_rejects_bad_input(app, email, password, error):
    result = app.test_cli_runner().invoke(args=["create-admin", "--email", email, "--password", password])

    assert result.exit_code != 0
    assert error in result.output
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Account.id))) == 1


def test_commands_explain_a_database_without_tables(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-only-secret")
    empty = create_app({"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'empty.db'}"})

    for args in (["seed-demo"], ["create-admin", "--email", "a@peoplebase.test", "--password", NEW_PASSWORD]):
        result = empty.test_cli_runner().invoke(args=args)
        assert result.exit_code != 0
        assert "flask --app app db upgrade" in result.output


def test_hash_password_never_returns_the_password(app):
    with app.app_context():
        assert TEST_PASSWORD not in hash_password(TEST_PASSWORD)
