"""Performance reviews. Abuse cases 22 and 23 in docs/security.md: reading
reviews that are not yours, and writing a review you may not write."""

import pytest

from app import AuditEvent, Review, ReviewCycle, db
from tests.conftest import csrf_from
from tests.test_audit import entries, last


def post(client, path, **data):
    return client.post(path, data={"csrf_token": csrf_from(client, "/"), **data})


@pytest.fixture()
def cycle(app, staff, client):
    """HR opens a cycle. Returns nothing; use review_id() to find a person's review."""
    response = post(client, "/reviews/cycles/new", name="2026 annual", self_due="2026-12-01", manager_due="2026-12-15")
    assert response.status_code == 302


def review_id(app, staff, person):
    with app.app_context():
        return db.session.scalar(db.select(Review.id).where(Review.employee_id == staff[person]))


def state(app, staff, person):
    with app.app_context():
        r = db.session.scalar(db.select(Review).where(Review.employee_id == staff[person]))
        return {
            "self": (r.self_rating, r.self_text),
            "manager": (r.manager_rating, r.manager_text, r.manager_submitted_by),
        }


GOOD = {"rating": "4", "text": "Shipped the migration and mentored two people."}


def submit_self(app, staff, sign_in, person="casey", **overrides):
    client = sign_in(f"{person}@peoplebase.test")
    return post(client, f"/reviews/{review_id(app, staff, person)}/self", **{**GOOD, **overrides})


def submit_manager(app, staff, sign_in, manager="riley", person="casey", **overrides):
    client = sign_in(f"{manager}@peoplebase.test")
    return post(client, f"/reviews/{review_id(app, staff, person)}/manager", **{**GOOD, **overrides})


# Opening and closing a cycle


def test_opening_a_cycle_creates_a_review_for_everyone_on_staff(app, staff, cycle):
    with app.app_context():
        reviewed = set(db.session.scalars(db.select(Review.employee_id)))
    assert reviewed == set(staff.values()) | {1}
    assert last(app, "review.cycle_opened")["details"] == {"people": 8}


def test_a_cycle_needs_a_name_and_sensible_dates(client):
    base = {"name": "Annual", "self_due": "2026-12-01", "manager_due": "2026-12-15"}
    for change in ({"name": ""}, {"self_due": ""}, {"manager_due": "nope"}, {"manager_due": "2026-11-01"}):
        assert post(client, "/reviews/cycles/new", **{**base, **change}).status_code == 400


def test_only_hr_opens_or_closes_cycles(app, staff, sign_in, cycle):
    for who in ("casey", "riley"):
        c = sign_in(f"{who}@peoplebase.test")
        new = {"name": "x", "self_due": "2026-12-01", "manager_due": "2026-12-02"}
        assert post(c, "/reviews/cycles/new", **new).status_code == 403
        assert post(c, "/reviews/cycles/1/close").status_code == 403
    with app.app_context():
        assert db.session.get(ReviewCycle, 1).is_open


def test_closing_a_cycle_is_recorded_and_freezes_writing(app, staff, client, sign_in, cycle):
    post(client, "/reviews/cycles/1/close")

    assert last(app, "review.cycle_closed")["subject"] == "2026 annual"
    assert submit_self(app, staff, sign_in).status_code == 403
    assert state(app, staff, "casey")["self"] == (None, "")


# The normal path


def test_self_then_manager_then_close_then_the_person_reads_both(app, staff, client, sign_in, cycle):
    assert submit_self(app, staff, sign_in).status_code == 302
    assert submit_manager(app, staff, sign_in, rating="3", text="Solid year.").status_code == 302
    rid = review_id(app, staff, "casey")
    casey = sign_in("casey@peoplebase.test")

    before_close = casey.get(f"/reviews/{rid}").data.decode()
    assert "Solid year." not in before_close and "Shipped the migration" in before_close

    post(client, "/reviews/cycles/1/close")
    after_close = casey.get(f"/reviews/{rid}").data.decode()
    assert "Solid year." in after_close and "riley@peoplebase.test" in after_close
    assert state(app, staff, "casey")["manager"] == (3, "Solid year.", "riley@peoplebase.test")
    assert last(app, "review.manager_submitted")["actor"] == "riley@peoplebase.test"


def test_a_manager_cannot_write_before_the_self_review_is_in(app, staff, sign_in, cycle):
    assert submit_manager(app, staff, sign_in).status_code == 403
    assert state(app, staff, "casey")["manager"] == (None, "", None)


# Abuse case 22: reading


def test_an_employee_cannot_read_a_peers_review(app, staff, sign_in, cycle):
    submit_self(app, staff, sign_in, "jordan")
    casey = sign_in("casey@peoplebase.test")

    assert casey.get(f"/reviews/{review_id(app, staff, 'jordan')}").status_code == 404


@pytest.mark.parametrize(
    ("reader", "expected"),
    [("riley", 200), ("morgan", 404), ("harper", 404), ("jordan", 404), ("emerson", 200), ("casey", 200)],
)
def test_who_can_open_a_review(app, staff, sign_in, cycle, reader, expected):
    """Only the person, their direct manager and HR. A skip-level manager cannot."""
    response = sign_in(f"{reader}@peoplebase.test").get(f"/reviews/{review_id(app, staff, 'casey')}")

    assert response.status_code == expected


def test_a_missing_review_and_a_forbidden_one_look_the_same(app, staff, sign_in, cycle):
    casey = sign_in("casey@peoplebase.test")

    hidden = casey.get(f"/reviews/{review_id(app, staff, 'jordan')}")
    missing = casey.get("/reviews/99999")

    assert hidden.status_code == missing.status_code == 404
    assert hidden.data == missing.data


def test_the_manager_review_stays_hidden_from_the_person_until_close(app, staff, sign_in, cycle):
    submit_self(app, staff, sign_in)
    submit_manager(app, staff, sign_in, text="Private until the cycle closes.")

    body = sign_in("casey@peoplebase.test").get(f"/reviews/{review_id(app, staff, 'casey')}").data.decode()

    assert "Private until the cycle closes." not in body
    assert "when HR closes the cycle" in body


def test_a_manager_cannot_read_the_self_review_before_it_is_submitted(app, staff, sign_in, cycle):
    body = sign_in("riley@peoplebase.test").get(f"/reviews/{review_id(app, staff, 'casey')}").data.decode()

    assert "Not submitted yet" in body


def test_reading_someone_elses_review_is_audited_and_your_own_is_not(app, staff, client, sign_in, cycle):
    sign_in("casey@peoplebase.test").get(f"/reviews/{review_id(app, staff, 'casey')}")
    assert entries(app, "view.review") == []

    sign_in("riley@peoplebase.test").get(f"/reviews/{review_id(app, staff, 'casey')}")
    client.get(f"/reviews/{review_id(app, staff, 'casey')}")

    assert [e["actor"] for e in entries(app, "view.review")] == ["riley@peoplebase.test", "hr@peoplebase.test"]


def test_review_lists_are_scoped(app, staff, client, sign_in, cycle):
    casey = sign_in("casey@peoplebase.test").get("/reviews").data.decode()
    riley = sign_in("riley@peoplebase.test").get("/reviews").data.decode()
    hr = client.get("/reviews").data.decode()

    assert "Jordan Example" not in casey and "Reviews you write" not in casey
    assert "Casey Example" in riley and "Jordan Example" in riley and "Rowan Example" not in riley
    assert "Rowan Example" in hr and "Close cycle" in hr


# Abuse case 23: writing


def test_an_employee_cannot_write_a_colleagues_self_review(app, staff, sign_in, cycle):
    casey = sign_in("casey@peoplebase.test")

    response = post(casey, f"/reviews/{review_id(app, staff, 'jordan')}/self", **GOOD)

    assert response.status_code == 404
    assert state(app, staff, "jordan")["self"] == (None, "")


def test_a_self_review_cannot_be_changed_after_it_is_submitted(app, staff, sign_in, cycle):
    submit_self(app, staff, sign_in)

    again = submit_self(app, staff, sign_in, rating="1", text="Rewritten after seeing the manager review.")

    assert again.status_code == 403
    assert state(app, staff, "casey")["self"] == (4, GOOD["text"])


def test_an_employee_cannot_write_the_manager_half_of_their_own_review(app, staff, sign_in, cycle):
    submit_self(app, staff, sign_in)
    casey = sign_in("casey@peoplebase.test")

    response = post(casey, f"/reviews/{review_id(app, staff, 'casey')}/manager", rating="5", text="Outstanding.")

    assert response.status_code == 403
    assert state(app, staff, "casey")["manager"] == (None, "", None)


def test_manager_fields_in_the_self_form_are_ignored(app, staff, sign_in, cycle):
    submit_self(app, staff, sign_in, manager_rating="5", manager_text="I rate myself.", manager_submitted_by="x")

    assert state(app, staff, "casey")["manager"] == (None, "", None)


@pytest.mark.parametrize("manager", ["morgan", "harper", "jordan"])
def test_only_the_direct_manager_writes_the_manager_review(app, staff, sign_in, cycle, manager):
    """A skip-level manager, another team's manager and a peer are all refused."""
    submit_self(app, staff, sign_in)

    response = submit_manager(app, staff, sign_in, manager=manager)

    assert response.status_code in (403, 404)
    assert state(app, staff, "casey")["manager"] == (None, "", None)


def test_hr_cannot_write_a_review_for_someone_else(app, staff, client, sign_in, cycle):
    submit_self(app, staff, sign_in)

    assert post(client, f"/reviews/{review_id(app, staff, 'casey')}/manager", **GOOD).status_code == 403
    assert post(client, f"/reviews/{review_id(app, staff, 'casey')}/self", **GOOD).status_code == 403


def test_a_manager_cannot_review_themselves(app, staff, sign_in, cycle):
    submit_self(app, staff, sign_in, "riley")

    assert submit_manager(app, staff, sign_in, manager="riley", person="riley").status_code == 403


@pytest.mark.parametrize(
    "bad",
    [
        {"rating": ""},
        {"rating": "0"},
        {"rating": "6"},
        {"rating": "-1"},
        {"rating": "４"},
        {"rating": "4.5"},
        {"text": ""},
        {"text": "   "},
        {"text": "x" * 4001},
        {"text": "bad\x00text"},
    ],
)
def test_ratings_and_text_are_validated(app, staff, sign_in, cycle, bad):
    assert submit_self(app, staff, sign_in, **bad).status_code == 400
    assert state(app, staff, "casey")["self"] == (None, "")


def test_review_text_is_shown_as_plain_text(app, staff, sign_in, cycle):
    submit_self(app, staff, sign_in, text="<script>alert(1)</script> line one\nline two")

    body = sign_in("casey@peoplebase.test").get(f"/reviews/{review_id(app, staff, 'casey')}").data.decode()

    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


def test_review_posts_need_csrf(app, staff, sign_in, cycle):
    casey = sign_in("casey@peoplebase.test")

    response = casey.post(f"/reviews/{review_id(app, staff, 'casey')}/self", data=GOOD)

    assert response.status_code == 400
    assert state(app, staff, "casey")["self"] == (None, "")


def test_deleting_an_employee_removes_their_reviews(app, staff, client, cycle):
    post(client, f"/employees/{staff['rowan']}/delete")

    with app.app_context():
        assert db.session.scalar(db.select(Review).where(Review.employee_id == staff["rowan"])) is None
        assert db.session.scalar(db.select(db.func.count(AuditEvent.id))) > 0


def test_submissions_are_audited(app, staff, sign_in, cycle):
    submit_self(app, staff, sign_in)

    entry = last(app, "review.self_submitted")
    assert entry["actor"] == "casey@peoplebase.test"
    assert entry["subject"] == "Casey Example, 2026 annual"
    assert "Shipped the migration" not in str(entry["details"])  # the text never goes in the log
