"""Employee documents. Abuse cases 24 to 27 in docs/security.md: reading
documents you may not read, uploading files that are not what they claim,
path tricks in file names, and oversized uploads."""

import io
from pathlib import Path

import pytest

import documents
from app import Document, db
from tests.conftest import csrf_from
from tests.test_audit import entries, last

PDF = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def upload(client, employee_id, data=PDF, filename="contract.pdf", **fields):
    form = {"csrf_token": csrf_from(client, "/"), "title": "Employment contract", **fields}
    if data is not None:
        form["file"] = (io.BytesIO(data), filename)
    return client.post(
        f"/employees/{employee_id}/documents", data=form, content_type="multipart/form-data"
    )


def stored(app):
    with app.app_context():
        root = Path(app.config["UPLOAD_ROOT"])
        return sorted(p.name for p in (root / "uploads").glob("*")) if (root / "uploads").exists() else []


def doc(app, **filters):
    with app.app_context():
        found = db.session.scalar(db.select(Document).filter_by(**filters))
        if found is not None:
            db.session.expunge(found)
        return found


@pytest.fixture()
def shared(app, staff, client):
    """Casey has three documents: HR only, the person and HR, and shared with managers."""
    for audience, title in (("hr", "Disciplinary note"), ("employee", "Offer letter"), ("manager", "Signed contract")):
        assert upload(client, staff["casey"], title=title, audience=audience).status_code == 302
    return {audience: doc(app, audience=audience).id for audience in ("hr", "employee", "manager")}


# The normal path


def test_hr_uploads_and_the_person_downloads_it(app, staff, client, sign_in):
    response = upload(client, staff["casey"], category="contract", audience="employee")

    assert response.status_code == 302
    stored_doc = doc(app, employee_id=staff["casey"])
    assert (stored_doc.title, stored_doc.category) == ("Employment contract", "contract")
    assert stored_doc.audience == "employee"
    assert stored_doc.content_type == "application/pdf" and stored_doc.size == len(PDF)

    download = sign_in("casey@peoplebase.test").get(f"/documents/{stored_doc.id}/download")

    assert download.status_code == 200
    assert download.data == PDF
    assert download.headers["Content-Disposition"].startswith("attachment")
    assert "contract.pdf" in download.headers["Content-Disposition"]
    assert download.headers["X-Content-Type-Options"] == "nosniff"
    assert download.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    ("data", "name", "kind"),
    [
        (PDF, "a.pdf", "application/pdf"),
        (PNG, "a.PNG", "image/png"),
        (JPEG, "a.jpeg", "image/jpeg"),
        (JPEG, "a.jpg", "image/jpeg"),
    ],
)
def test_pdf_png_and_jpeg_are_accepted(app, staff, client, data, name, kind):
    assert upload(client, staff["casey"], data, name).status_code == 302
    assert doc(app, employee_id=staff["casey"]).content_type == kind


def test_an_employee_can_add_their_own_document_but_only_shared_with_hr(app, staff, sign_in):
    casey = sign_in("casey@peoplebase.test")

    assert upload(casey, staff["casey"], audience="employee").status_code == 302
    assert doc(app, employee_id=staff["casey"]).uploaded_by == "casey@peoplebase.test"


def test_an_employee_cannot_choose_a_wider_audience_for_their_own_document(app, staff, sign_in):
    casey = sign_in("casey@peoplebase.test")

    for audience in ("manager", "hr", "everyone"):
        assert upload(casey, staff["casey"], audience=audience).status_code == 400
    assert stored(app) == []


# Abuse case 24: reading documents you may not read


@pytest.mark.parametrize(
    ("reader", "audience", "expected"),
    [
        ("emerson", "hr", 200), ("emerson", "employee", 200), ("emerson", "manager", 200),
        ("casey", "hr", 404), ("casey", "employee", 200), ("casey", "manager", 200),
        ("riley", "hr", 404), ("riley", "employee", 404), ("riley", "manager", 200),
        ("morgan", "hr", 404), ("morgan", "employee", 404), ("morgan", "manager", 200),
        ("harper", "hr", 404), ("harper", "employee", 404), ("harper", "manager", 404),
        ("jordan", "hr", 404), ("jordan", "employee", 404), ("jordan", "manager", 404),
    ],
)
def test_who_can_download_which_document(app, staff, sign_in, shared, reader, audience, expected):
    response = sign_in(f"{reader}@peoplebase.test").get(f"/documents/{shared[audience]}/download")

    assert response.status_code == expected


def test_a_refused_download_looks_like_a_missing_one(staff, sign_in, shared):
    jordan = sign_in("jordan@peoplebase.test")

    hidden = jordan.get(f"/documents/{shared['employee']}/download")
    missing = jordan.get("/documents/99999/download")

    assert hidden.status_code == missing.status_code == 404
    assert hidden.data == missing.data


def test_the_list_shows_only_what_the_viewer_may_read(staff, client, sign_in, shared):
    path = f"/employees/{staff['casey']}/documents"

    hr = client.get(path).data.decode()
    own = sign_in("casey@peoplebase.test").get(path).data.decode()
    manager = sign_in("riley@peoplebase.test").get(path).data.decode()

    assert "Disciplinary note" in hr and "Offer letter" in hr and "Signed contract" in hr
    assert "Disciplinary note" not in own and "Offer letter" in own and "Signed contract" in own
    assert "Offer letter" not in manager and "Signed contract" in manager and "Disciplinary note" not in manager


def test_a_peer_and_another_teams_manager_cannot_open_the_list(staff, sign_in, shared):
    path = f"/employees/{staff['casey']}/documents"

    assert sign_in("jordan@peoplebase.test").get(path).status_code == 404
    assert sign_in("harper@peoplebase.test").get(path).status_code == 404


def test_nobody_but_hr_and_the_person_can_upload_to_a_record(app, staff, sign_in):
    assert upload(sign_in("riley@peoplebase.test"), staff["casey"]).status_code == 403
    assert upload(sign_in("jordan@peoplebase.test"), staff["casey"]).status_code == 404
    assert upload(sign_in("harper@peoplebase.test"), staff["casey"]).status_code == 404
    assert stored(app) == []


def test_only_hr_can_delete_and_it_removes_the_file(app, staff, client, sign_in, shared):
    manager_doc = shared["manager"]

    def delete(who, document_id):
        return who.post(f"/documents/{document_id}/delete", data={"csrf_token": csrf_from(who, "/")})

    assert delete(sign_in("riley@peoplebase.test"), manager_doc).status_code == 403
    assert delete(sign_in("casey@peoplebase.test"), shared["hr"]).status_code == 404
    assert len(stored(app)) == 3

    response = delete(client, manager_doc)

    assert response.status_code == 302
    assert len(stored(app)) == 2 and doc(app, id=manager_doc) is None


def test_deleting_an_employee_removes_their_files(app, staff, client, shared):
    client.post(f"/employees/{staff['casey']}/delete", data={"csrf_token": csrf_from(client, "/")})

    assert stored(app) == [] and doc(app, employee_id=staff["casey"]) is None


def test_uploads_and_downloads_are_audited_without_file_contents(app, staff, client, sign_in, shared):
    sign_in("riley@peoplebase.test").get(f"/documents/{shared['manager']}/download")
    sign_in("casey@peoplebase.test").get(f"/documents/{shared['employee']}/download")

    assert [e["actor"] for e in entries(app, "view.document")] == ["riley@peoplebase.test"]
    uploaded = last(app, "document.uploaded")
    assert uploaded["actor"] == "hr@peoplebase.test" and uploaded["details"]["person"] == "Casey Example"
    assert "%PDF" not in str(entries(app))


# Abuse case 25: type spoofing


@pytest.mark.parametrize(
    ("data", "name"),
    [
        (b"<html><script>alert(1)</script></html>", "evil.pdf"),   # HTML called a PDF
        (PNG, "image.pdf"),                                        # a real PNG with the wrong extension
        (PDF, "scan.png"),                                         # a real PDF named .png
        (PDF, "page.html"),                                        # right bytes, forbidden extension
        (PDF, "run.exe"),
        (PDF, "archive.pdf.exe"),
        (PDF, "noextension"),
        (b"GIF89a" + b"\x00" * 20, "pic.gif"),
        (b"PK\x03\x04" + b"\x00" * 20, "doc.pdf"),                 # a zip called a PDF
        (b"", "empty.pdf"),
    ],
)
def test_files_that_are_not_what_they_claim_are_refused(app, staff, client, data, name):
    assert upload(client, staff["casey"], data, name).status_code == 400
    assert stored(app) == [] and doc(app, employee_id=staff["casey"]) is None


def test_a_missing_file_field_is_refused(app, staff, client):
    assert upload(client, staff["casey"], data=None).status_code == 400


def test_the_content_type_comes_from_the_bytes_not_the_request(app, staff, client):
    response = client.post(
        f"/employees/{staff['casey']}/documents",
        data={
            "csrf_token": csrf_from(client, "/"),
            "file": (io.BytesIO(PDF), "x.pdf", "text/html"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 302
    assert doc(app, employee_id=staff["casey"]).content_type == "application/pdf"


# Abuse case 26: path tricks


@pytest.mark.parametrize(
    "name",
    [
        "../../../etc/passwd.pdf",
        "..\\..\\windows\\system32\\x.pdf",
        "/absolute/path/evil.pdf",
        "C:\\temp\\evil.pdf",
        "a/b/c/../../../../../x.pdf",
        "name\x00.pdf.exe",
        "...pdf",
    ],
)
def test_file_names_cannot_choose_where_the_file_goes(app, staff, client, name):
    upload(client, staff["casey"], PDF, name)

    root = Path(app.config["UPLOAD_ROOT"]).resolve()
    on_disk = [p for p in root.rglob("*") if p.is_file()]
    assert all(p.parent == root / "uploads" for p in on_disk)
    assert all(documents.STORED_NAME.fullmatch(p.name) for p in on_disk)
    saved = doc(app, employee_id=staff["casey"])
    if saved is not None:
        assert "/" not in saved.original_name and "\\" not in saved.original_name and "\x00" not in saved.original_name


def test_stored_names_are_random_and_ignore_the_original_name(app, staff, client):
    upload(client, staff["casey"], PDF, "contract.pdf")
    upload(client, staff["casey"], PDF, "contract.pdf")

    names = stored(app)
    assert len(names) == len(set(names)) == 2
    assert all("contract" not in n for n in names)


def test_the_download_path_ignores_a_tampered_stored_name(app, staff, client, shared):
    with app.app_context():
        record = db.session.get(Document, shared["employee"])
        record.stored_name = "../../../../etc/passwd"
        db.session.commit()

    assert client.get(f"/documents/{shared['employee']}/download").status_code == 404


@pytest.mark.parametrize("bad", ["", "../x.pdf", "a" * 32 + ".exe", "A" * 32 + ".pdf", "x.pdf", None])
def test_path_for_refuses_names_it_did_not_make(tmp_path, bad):
    assert documents.path_for(tmp_path, bad) is None


def test_uploads_live_outside_static(app):
    assert not str(Path(app.config["UPLOAD_ROOT"]).resolve()).startswith(str(Path(app.static_folder).resolve()))


# Abuse case 27: size


def test_a_file_over_the_limit_is_refused_and_nothing_is_stored(app, staff, client):
    big = PDF + b"0" * (documents.MAX_UPLOAD_BYTES + 1)

    response = upload(client, staff["casey"], big)

    assert response.status_code in (400, 413)
    assert stored(app) == []


def test_a_file_at_the_limit_is_accepted(app, staff, client):
    edge = PDF + b"0" * (documents.MAX_UPLOAD_BYTES - len(PDF))

    assert upload(client, staff["casey"], edge).status_code == 302
    assert doc(app, employee_id=staff["casey"]).size == documents.MAX_UPLOAD_BYTES


def test_other_routes_keep_the_small_request_limit(client):
    response = client.post("/employees/new", data={"csrf_token": "x", "name": "a" * (70 * 1024)})

    assert response.status_code == 413


def test_anonymous_uploads_do_not_get_the_larger_limit(app, staff, anonymous):
    response = anonymous.post(
        f"/employees/{staff['casey']}/documents",
        data={"file": (io.BytesIO(PDF + b"0" * 200_000), "x.pdf")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 413


def test_a_record_holds_a_limited_number_of_documents(app, staff, client, monkeypatch):
    monkeypatch.setattr(documents, "MAX_DOCUMENTS_PER_EMPLOYEE", 2)
    for _ in range(2):
        assert upload(client, staff["casey"]).status_code == 302

    assert upload(client, staff["casey"]).status_code == 400
    assert len(stored(app)) == 2


# Forms


def test_upload_and_delete_need_csrf(app, staff, client, shared):
    no_token = client.post(
        f"/employees/{staff['casey']}/documents",
        data={"file": (io.BytesIO(PDF), "x.pdf")},
        content_type="multipart/form-data",
    )
    assert no_token.status_code == 400
    assert client.post(f"/documents/{shared['hr']}/delete", data={}).status_code == 400
    assert len(stored(app)) == 3


def test_documents_are_never_deleted_by_a_get(client, shared):
    assert client.get(f"/documents/{shared['hr']}/delete").status_code == 405


def test_audit_entries_survive_a_deleted_document(app, staff, client, shared):
    client.post(f"/documents/{shared['hr']}/delete", data={"csrf_token": csrf_from(client, "/")})

    assert len(entries(app, "document.deleted")) == 1
