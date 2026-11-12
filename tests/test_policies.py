"""Policies and acknowledgements. Abuse cases 15 and 16 in docs/security.md."""

import pytest
from sqlalchemy.exc import IntegrityError

from app import Acknowledgement, Employee, Policy, db
from tests.conftest import HR_EMAIL, csrf_from
from tests.test_audit import entries

TEXT = "Lock your laptop when you step away.\n\nReport lost equipment the same day."


def post(client, path, **data):
    return client.post(path, data={"csrf_token": csrf_from(client, "/"), **data})


@pytest.fixture()
def policy(app, staff):
    with app.app_context():
        item = Policy(title="Equipment", body=TEXT, published_by=HR_EMAIL)
        db.session.add(item)
        db.session.commit()
        return item.id


def acknowledgers(app, policy_id):
    with app.app_context():
        rows = db.session.scalars(db.select(Acknowledgement).where(Acknowledgement.policy_id == policy_id))
        return sorted(row.employee.name.split()[0].lower() for row in rows)


# Publishing


def test_hr_publishes_a_policy(app, client):
    response = post(client, "/policies/new", title="  Equipment ", body="Line one.\r\n\r\nLine two.\r\n")

    assert response.status_code == 302
    with app.app_context():
        published = db.session.scalar(db.select(Policy))
        assert (published.title, published.body) == ("Equipment", "Line one.\n\nLine two.")
        assert published.published_by == HR_EMAIL and published.current
    assert entries(app, "policy.published")[0]["subject"] == "Equipment"


@pytest.mark.parametrize(
    "fields,error",
    [
        ({"title": ""}, b"Title is required"),
        ({"title": "x" * 121}, b"120 characters"),
        ({"title": "Two\nlines"}, b"control characters"),
        ({"body": "   "}, b"Policy text is required"),
        ({"body": "x" * 20001}, b"20,000 characters"),
        ({"body": "bell\x07"}, b"control characters"),
    ],
)
def test_invalid_policies_are_rejected(app, client, fields, error):
    response = post(client, "/policies/new", **{"title": "Equipment", "body": TEXT, **fields})

    assert response.status_code == 400
    assert error in response.data
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Policy.id))) == 0


def test_policy_text_is_shown_as_text_never_as_markup(client):
    post(client, "/policies/new", title="<b>Bold</b>", body="<script>alert(1)</script>\n<img src=x onerror=alert(1)>")

    page = client.get("/policies/1").data

    assert b"<script>alert(1)</script>" not in page
    assert b"&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert b"<img src=x" not in page
    assert b"<b>Bold</b>" not in client.get("/policies").data


@pytest.mark.parametrize("who", ["casey", "riley"])
def test_only_hr_publishes_revises_and_retires(app, staff, sign_in, policy, who):
    client = sign_in(f"{who}@peoplebase.test")

    assert post(client, "/policies/new", title="Mine", body="Do as I say.").status_code == 403
    assert post(client, f"/policies/{policy}/revise", title="Equipment", body="Changed.").status_code == 403
    assert post(client, f"/policies/{policy}/retire").status_code == 403
    assert client.get(f"/policies/{policy}/revise").status_code == 403
    with app.app_context():
        only = db.session.scalar(db.select(Policy))
        assert only.body == TEXT and only.current


# Acknowledging


def test_an_employee_reads_and_acknowledges(app, staff, sign_in, policy):
    casey = sign_in("casey@peoplebase.test")
    assert b"Needs your acknowledgement" in casey.get("/policies").data
    assert b"Lock your laptop" in casey.get(f"/policies/{policy}").data

    response = post(casey, f"/policies/{policy}/acknowledge")

    assert response.status_code == 302
    assert acknowledgers(app, policy) == ["casey"]
    assert b"Acknowledged" in casey.get("/policies").data
    assert b"I have read this policy" not in casey.get(f"/policies/{policy}").data
    entry = entries(app, "policy.acknowledged")[0]
    assert (entry["actor"], entry["subject"]) == ("casey@peoplebase.test", "Equipment")


def test_acknowledging_twice_is_recorded_once(app, staff, sign_in, policy):
    casey = sign_in("casey@peoplebase.test")

    post(casey, f"/policies/{policy}/acknowledge")
    post(casey, f"/policies/{policy}/acknowledge")

    assert acknowledgers(app, policy) == ["casey"]
    assert len(entries(app, "policy.acknowledged")) == 1


def test_nobody_acknowledges_for_someone_else(app, staff, sign_in, policy):
    post(
        sign_in("casey@peoplebase.test"),
        f"/policies/{policy}/acknowledge",
        employee_id=str(staff["jordan"]), employee=str(staff["jordan"]), acknowledged_by="jordan@peoplebase.test",
    )

    assert acknowledgers(app, policy) == ["casey"]


def test_hr_cannot_acknowledge_for_staff_either(app, staff, sign_in, policy):
    post(sign_in("emerson@peoplebase.test"), f"/policies/{policy}/acknowledge", employee_id=str(staff["casey"]))

    assert acknowledgers(app, policy) == ["emerson"]


def test_account_without_an_employee_record_has_nothing_to_sign(app, client, policy):
    response = post(client, f"/policies/{policy}/acknowledge")

    assert response.status_code == 400
    assert acknowledgers(app, policy) == []
    assert b"nothing for you to sign" in client.get(f"/policies/{policy}").data


def test_acknowledging_needs_csrf_and_post(app, staff, sign_in, policy):
    casey = sign_in("casey@peoplebase.test")

    assert casey.post(f"/policies/{policy}/acknowledge").status_code == 400
    assert casey.get(f"/policies/{policy}/acknowledge").status_code == 405
    assert acknowledgers(app, policy) == []


def test_database_refuses_a_duplicate_acknowledgement(app, staff, policy):
    with app.app_context():
        db.session.add(Acknowledgement(policy_id=policy, employee_id=staff["casey"]))
        db.session.commit()
        db.session.add(Acknowledgement(policy_id=policy, employee_id=staff["casey"]))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


# Progress, for HR only


def test_hr_sees_who_has_and_has_not_acknowledged(app, staff, sign_in, policy):
    post(sign_in("casey@peoplebase.test"), f"/policies/{policy}/acknowledge")
    with app.app_context():
        db.session.get(Employee, staff["rowan"]).status = "terminated"
        db.session.commit()
    hr = sign_in("emerson@peoplebase.test")

    listing, detail = hr.get("/policies").data, hr.get(f"/policies/{policy}").data

    assert b"1 of 7</span> on staff have acknowledged" in listing  # Rowan left; Avery from the base fixture counts
    assert b"1 of 7 on staff have acknowledged" in detail
    assert b"Rowan Example" not in detail
    assert detail.count(b"Waiting") == 6


def test_staff_do_not_see_who_else_has_acknowledged(app, staff, sign_in, policy):
    post(sign_in("jordan@peoplebase.test"), f"/policies/{policy}/acknowledge")

    for who in ("casey", "riley"):
        client = sign_in(f"{who}@peoplebase.test")
        listing, detail = client.get("/policies").data, client.get(f"/policies/{policy}").data
        assert b"on staff have acknowledged" not in listing + detail
        assert b"Jordan Example" not in listing + detail
        assert HR_EMAIL.encode() not in detail


# Revising and retiring


def test_revising_publishes_a_new_version_and_asks_everyone_again(app, client, staff, sign_in, policy):
    casey = sign_in("casey@peoplebase.test")
    post(casey, f"/policies/{policy}/acknowledge")

    response = post(client, f"/policies/{policy}/revise", title="Equipment", body="New rules.")

    assert response.headers["Location"] == "/policies/2"
    with app.app_context():
        old, new = db.session.get(Policy, policy), db.session.get(Policy, 2)
        assert not old.current and old.body == TEXT       # the text people agreed to is untouched
        assert new.current and new.replaces_id == old.id and new.body == "New rules."
    assert acknowledgers(app, policy) == ["casey"]        # kept, against the old text
    assert acknowledgers(app, 2) == []
    assert b"Needs your acknowledgement" in casey.get("/policies").data
    assert entries(app, "policy.revised")[0]["details"] == {"replaces": policy}


def test_revising_without_a_change_keeps_the_acknowledgements_valid(app, client, staff, sign_in, policy):
    post(sign_in("casey@peoplebase.test"), f"/policies/{policy}/acknowledge")

    post(client, f"/policies/{policy}/revise", title="Equipment", body=TEXT)

    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Policy.id))) == 1


def test_old_versions_are_closed_to_staff_and_cannot_be_acknowledged(app, client, staff, sign_in, policy):
    post(client, f"/policies/{policy}/revise", title="Equipment", body="New rules.")
    casey = sign_in("casey@peoplebase.test")

    assert casey.get(f"/policies/{policy}").status_code == 404
    assert post(casey, f"/policies/{policy}/acknowledge").status_code == 404
    assert client.get(f"/policies/{policy}").status_code == 200
    assert post(sign_in("emerson@peoplebase.test"), f"/policies/{policy}/acknowledge").status_code == 400
    assert post(client, f"/policies/{policy}/revise", title="Equipment", body="Again.").status_code == 400
    assert acknowledgers(app, policy) == []


def test_retiring_a_policy_keeps_its_acknowledgements(app, client, staff, sign_in, policy):
    casey = sign_in("casey@peoplebase.test")
    post(casey, f"/policies/{policy}/acknowledge")
    assert b"Equipment" in casey.get("/policies").data

    post(client, f"/policies/{policy}/retire")
    post(client, f"/policies/{policy}/retire")

    assert b"Equipment" not in casey.get("/policies").data
    assert acknowledgers(app, policy) == ["casey"]
    assert len(entries(app, "policy.retired")) == 1


def test_dashboards_point_at_policies_still_to_acknowledge(app, staff, sign_in, policy):
    casey, hr = sign_in("casey@peoplebase.test"), sign_in("emerson@peoplebase.test")

    assert b"Policies waiting for you" in casey.get("/").data
    assert b"1 policy needs your acknowledgement" in hr.get("/").data

    post(casey, f"/policies/{policy}/acknowledge")
    assert b"Policies waiting for you" not in casey.get("/").data


def test_deleting_an_employee_deletes_their_acknowledgements(app, client, staff, sign_in, policy):
    post(sign_in("casey@peoplebase.test"), f"/policies/{policy}/acknowledge")

    post(client, f"/employees/{staff['casey']}/delete")

    assert acknowledgers(app, policy) == []


def test_demo_data_has_policies_with_some_acknowledgements_outstanding(app):
    with app.app_context():
        db.session.delete(db.session.get(Employee, 1))
        db.session.commit()
    app.test_cli_runner().invoke(args=["seed-demo"])

    with app.app_context():
        counts = {item.title: len(item.acknowledgements) for item in db.session.scalars(db.select(Policy))}
        assert counts == {"Code of conduct": 12, "Remote work and equipment": 9}
        assert all("demo text, not a real policy" in item.body for item in db.session.scalars(db.select(Policy)))
