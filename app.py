import csv
import hmac
import json
import math
import os
import re
import secrets
import time
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import wraps
from io import StringIO
from pathlib import Path
from urllib.parse import urlsplit

import click
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from flask import (
    Flask,
    abort,
    current_app,
    flash,
    g,
    has_request_context,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_migrate import Migrate
from sqlalchemy import inspect
from werkzeug.exceptions import HTTPException, InternalServerError

from access import Access
from models import (
    AUDIT_ACTIONS,
    AUDIT_GROUPS,
    EMPLOYMENT_STATUSES,
    ROLES,
    TIME_OFF_DECISIONS,
    TIME_OFF_KINDS,
    TIME_OFF_STATUSES,
    Account,
    AuditEvent,
    CompensationChange,
    Department,
    Employee,
    LoginThrottle,
    TimeOffRequest,
    db,
    utcnow,
)

MAX_SALARY = Decimal("999999999.99")
DEFAULT_PER_PAGE = 25
AUDIT_PER_PAGE = 50
MAX_PER_PAGE = 100
CSRF_SESSION_KEY = "_csrf_token"
CSRF_FORM_FIELD = "csrf_token"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# Spreadsheet apps treat cells starting with these characters as formulas.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
# Plain ASCII decimal numbers, optionally in scientific notation. Decimal() on
# its own also accepts "1_000" and non-ASCII digits, which a form should not.
SALARY_PATTERN = re.compile(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?", re.ASCII)
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)
MIN_DATE = date(1900, 1, 1)
MAX_DATE = date(2100, 12, 31)
# Every form in the app is a few short fields; anything bigger is refused with 413.
MAX_CONTENT_LENGTH = 64 * 1024
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
MAX_TIME_OFF_DAYS = 60

# Sign-in. See docs/security.md for the reasoning behind each number.
SESSION_LIFETIME = timedelta(hours=12)
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128
EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
LOGIN_WINDOW = timedelta(minutes=15)
LOGIN_LOCK = timedelta(minutes=15)
# Failures inside one window before the key is locked.
LOGIN_LIMITS = {"email": 5, "ip": 20}
LOGIN_FAILED_MESSAGE = "Email or password is incorrect."
LOGIN_LOCKED_MESSAGE = "Too many failed sign-ins. Wait 15 minutes and try again."
# Endpoints that work without a session, and the ones an account with a
# temporary password may still reach.
PUBLIC_ENDPOINTS = frozenset({"login", "static"})
TEMPORARY_PASSWORD_ENDPOINTS = frozenset({"change_password", "logout", "static"})
MIGRATIONS_DIRECTORY = Path(__file__).resolve().parent / "migrations"

migrate = Migrate()


def create_app(test_config=None):
    app = Flask(__name__)
    database_path = Path(app.instance_path) / "employees.db"
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY"),
        SQLALCHEMY_DATABASE_URI=os.environ.get(
            "DATABASE_URL", f"sqlite:///{database_path}"
        ),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_HTTPONLY=True,
        # Set SESSION_COOKIE_SECURE=1 wherever the app is served over HTTPS.
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE") == "1",
        PERMANENT_SESSION_LIFETIME=SESSION_LIFETIME,
        # Argon2id cost settings; empty means the argon2-cffi defaults.
        # Tests pass cheaper ones so the suite stays fast.
        PASSWORD_HASH_OPTIONS={},
        MAX_CONTENT_LENGTH=MAX_CONTENT_LENGTH,
    )

    if test_config:
        app.config.update(test_config)

    if not app.config.get("SECRET_KEY"):
        raise RuntimeError(
            "SECRET_KEY is not set. Export a long random value before starting "
            "PeopleBase, for example: "
            "export SECRET_KEY=\"$(python -c 'import secrets; print(secrets.token_hex(32))')\""
        )

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    db.init_app(app)
    # The schema is owned by the versioned migrations in migrations/versions.
    # Nothing is created at startup: run "flask --app app db upgrade".
    migrate.init_app(app, db, directory=str(MIGRATIONS_DIRECTORY), render_as_batch=True, compare_type=True)
    hasher = PasswordHasher(**app.config["PASSWORD_HASH_OPTIONS"])
    app.extensions["password_hasher"] = hasher
    # Verified when the email has no account, so both cases cost one hash.
    app.extensions["password_decoy"] = hasher.hash(secrets.token_urlsafe(16))
    app.jinja_env.globals["csrf_token"] = get_csrf_token
    app.jinja_env.globals["ROLES"] = ROLES
    app.jinja_env.globals["EMPLOYMENT_STATUSES"] = EMPLOYMENT_STATUSES
    app.jinja_env.globals["TIME_OFF_KINDS"] = TIME_OFF_KINDS
    app.jinja_env.globals["TIME_OFF_STATUSES"] = TIME_OFF_STATUSES

    @app.before_request
    def load_account():
        """Resolve the session cookie to an account, or to nobody."""
        g.account = None
        g.access = Access(None)
        if request.endpoint == "static" or "account_id" not in session:
            return None
        account = db.session.get(Account, session.get("account_id"))
        if account is None or not session_is_current(account):
            session.clear()
            return None
        g.account = account
        g.access = Access(account)
        return None

    @app.before_request
    def protect_from_csrf():
        if request.method not in UNSAFE_METHODS:
            return None
        expected = session.get(CSRF_SESSION_KEY, "")
        submitted = request.form.get(CSRF_FORM_FIELD, "")
        if not expected or not hmac.compare_digest(
            expected.encode(), submitted.encode()
        ):
            abort(
                400,
                description="The form has expired or did not come from PeopleBase. "
                "Reload the page and try again.",
            )
        return None

    @app.before_request
    def require_sign_in():
        if request.endpoint in PUBLIC_ENDPOINTS or request.endpoint is None:
            return None
        if g.account is None:
            target = request.full_path.rstrip("?") if request.method == "GET" else None
            return redirect(url_for("login", next=target))
        if g.account.must_change_password and request.endpoint not in TEMPORARY_PASSWORD_ENDPOINTS:
            flash("Choose your own password before you continue.", "info")
            return redirect(url_for("change_password"))
        return None

    @app.context_processor
    def inject_account():
        return {"current_account": g.get("account"), "access": g.get("access") or Access(None)}

    @app.after_request
    def add_security_headers(response):
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        if request.endpoint != "static":
            # Pay and leave data must not come back from a browser or proxy cache.
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(HTTPException)
    def handle_http_error(error):
        return render_template("error.html", error=error), error.code

    @app.errorhandler(InternalServerError)
    def handle_server_error(error):
        # Never show exception details; the traceback goes to the server log.
        db.session.rollback()
        return render_template("error.html", error=InternalServerError()), 500

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.account is not None:
            return redirect(url_for("home"))
        if request.method == "GET":
            return render_template("login.html", next=safe_next(request.args.get("next")), email="")

        email = normalize_email(request.form.get("email", ""))
        password = request.form.get("password", "")
        next_url = safe_next(request.form.get("next"))
        now = utcnow()
        keys = {"email": f"email:{email}", "ip": f"ip:{request.remote_addr or 'unknown'}"}

        if any(throttle_locked(key, now) for key in keys.values()):
            flash(LOGIN_LOCKED_MESSAGE, "error")
            return render_template("login.html", next=next_url, email=email), 429

        account = db.session.scalar(db.select(Account).where(Account.email == email)) if email else None
        if not password_matches(account, password) or not may_sign_in(account):
            for name, key in keys.items():
                record_failed_login(key, LOGIN_LIMITS[name], now)
            # The email is recorded as typed. The password never is.
            audit("auth.sign_in_failed", label=email or "(no email)")
            db.session.commit()
            flash(LOGIN_FAILED_MESSAGE, "error")
            return render_template("login.html", next=next_url, email=email), 401

        clear_throttle(keys["email"])
        upgrade_password_hash(account, password)
        account.last_login_at = now
        audit("auth.signed_in", account, actor=account)
        db.session.commit()
        start_session(account)
        return redirect(next_url or url_for("home"))

    @app.post("/logout")
    def logout():
        # Rotating the nonce also ends this account's sessions in other browsers.
        g.account.end_sessions()
        audit("auth.signed_out", g.account)
        db.session.commit()
        session.clear()
        flash("You are signed out.", "success")
        return redirect(url_for("login"))

    @app.route("/account/password", methods=["GET", "POST"])
    def change_password():
        account = g.account
        if request.method == "POST":
            new_password, error = validate_new_password(request.form, account)
            if not error and not password_matches(account, request.form.get("current_password", "")):
                error = "Your current password is incorrect."
            if error:
                flash(error, "error")
                return render_template("change_password.html"), 400
            account.password_hash = hash_password(new_password)
            account.must_change_password = False
            account.end_sessions()
            audit("auth.password_changed", account)
            db.session.commit()
            # Every other session is now signed out; keep this one.
            start_session(account)
            flash("Your password is changed. Other sessions were signed out.", "success")
            return redirect(url_for("home"))
        return render_template("change_password.html")

    @app.get("/accounts")
    @admin_only
    def view_accounts():
        return render_accounts({})

    @app.post("/accounts")
    @admin_only
    def add_account():
        values, error = validate_account(request.form)
        if error:
            flash(error, "error")
            return render_accounts(request.form), 400
        password = new_temporary_password()
        account = Account(**values, password_hash=hash_password(password), must_change_password=True)
        db.session.add(account)
        audit(
            "account.created", account, role=account.role, employee=account.employee.name if account.employee else None
        )
        db.session.commit()
        flash(f"Created an account for {account.email}.", "success")
        return render_accounts({}, issued=(account, password)), 201

    @app.post("/accounts/<int:account_id>/role")
    @admin_only
    def change_account_role(account_id):
        account = db.get_or_404(Account, account_id)
        old_role = account.role
        error = apply_role_change(account, request.form.get("role", "").strip(), g.account)
        if error:
            flash(error, "error")
        else:
            if account.role != old_role:
                audit("account.role_changed", account, role=[old_role, account.role])
            db.session.commit()
            flash(f"{account.email} is now {account.role_label}.", "success")
        return redirect(url_for("view_accounts"))

    @app.post("/accounts/<int:account_id>/active")
    @admin_only
    def set_account_active(account_id):
        account = db.get_or_404(Account, account_id)
        was_active = account.active
        error = apply_active_change(account, request.form.get("active") == "1", g.account)
        if error:
            flash(error, "error")
        else:
            if account.active != was_active:
                audit("account.turned_on" if account.active else "account.turned_off", account)
            db.session.commit()
            flash(f"{account.email} {'can sign in again' if account.active else 'can no longer sign in'}.", "success")
        return redirect(url_for("view_accounts"))

    @app.post("/accounts/<int:account_id>/password")
    @admin_only
    def reset_account_password(account_id):
        account = db.get_or_404(Account, account_id)
        if account.id == g.account.id:
            flash("Change your own password from the Password page.", "error")
            return redirect(url_for("view_accounts"))
        password = new_temporary_password()
        account.password_hash = hash_password(password)
        account.must_change_password = True
        account.end_sessions()
        clear_throttle(f"email:{account.email}")
        audit("account.password_reset", account)
        db.session.commit()
        flash(f"Reset the password for {account.email}.", "success")
        return render_accounts({}, issued=(account, password))

    @app.get("/")
    def home():
        today = date.today()
        if not g.access.is_admin:
            return render_personal_home(today)
        employees = db.session.scalars(db.select(Employee).order_by(Employee.name)).all()
        current = [employee for employee in employees if employee.status != "terminated"]
        out_today = db.session.scalars(
            db.select(TimeOffRequest)
            .where(
                TimeOffRequest.status == "approved",
                TimeOffRequest.start_date <= today,
                TimeOffRequest.end_date >= today,
            )
            .order_by(TimeOffRequest.end_date)
        ).all()
        pending_count = db.session.scalar(
            db.select(db.func.count(TimeOffRequest.id)).where(TimeOffRequest.status == "pending")
        )
        return render_template(
            "home.html",
            employee_count=len(employees),
            status_counts={
                key: sum(1 for employee in employees if employee.status == key)
                for key in EMPLOYMENT_STATUSES
            },
            department_count=db.session.scalar(db.select(db.func.count(Department.id))),
            departments=headcount_by_department(current),
            anniversaries=upcoming_anniversaries(current, today),
            out_today=out_today,
            pending_count=pending_count,
            today=today,
        )

    @app.get("/org")
    def org_chart():
        employees = db.session.scalars(
            db.select(Employee)
            .where(Employee.status != "terminated")
            .order_by(Employee.name, Employee.id)
        ).all()
        return render_template("org.html", roots=build_org_tree(employees), total=len(employees))

    @app.get("/employees")
    def view_employees():
        criteria = directory_criteria(request.args, g.access)
        page = max(request.args.get("page", 1, type=int), 1)
        per_page = request.args.get("per_page", DEFAULT_PER_PAGE, type=int)
        per_page = min(max(per_page, 1), MAX_PER_PAGE)

        filters = directory_filters(criteria, g.access)
        summary = summarize_employees(filters)
        # Clamp to the last page before querying. A page past the end shows the
        # last page, and a huge page number never reaches the database as an
        # OFFSET too large for it to store.
        last_page = max(math.ceil(summary["matching_count"] / per_page), 1)
        page = min(page, last_page)
        statement = db.select(Employee).where(*filters).order_by(Employee.name, Employee.id)
        pagination = db.paginate(statement, page=page, per_page=per_page, error_out=False)

        positions = db.session.scalars(
            db.select(Employee.position)
            .where(*directory_filters({}, g.access))
            .distinct()
            .order_by(Employee.position)
        ).all()
        filter_args = {
            "q": criteria["search"] or None,
            "position": criteria["position"] or None,
            "department": criteria["department"] or None,
            "status": criteria["status"] or None,
        }
        return render_template(
            "view_employees.html",
            employees=pagination.items,
            pagination=pagination,
            page_args={
                **filter_args,
                "per_page": per_page if per_page != DEFAULT_PER_PAGE else None,
            },
            filter_args=filter_args,
            filtered=any(criteria.values()),
            positions=positions,
            departments=all_departments(),
            search=criteria["search"],
            selected_position=criteria["position"],
            selected_department=criteria["department"],
            selected_status=criteria["status"],
            **summary,
        )

    @app.get("/employees.csv")
    @admin_only
    def export_employees():
        # The export carries salary, so it is HR-only whatever the filters say.
        criteria = directory_criteria(request.args, g.access)
        employees = db.session.scalars(
            db.select(Employee)
            .where(*directory_filters(criteria, g.access))
            .order_by(Employee.name, Employee.id)
        ).all()
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(["id", "name", "position", "annual_salary"])
        for employee in employees:
            writer.writerow(
                [
                    employee.id,
                    neutralize_formula(employee.name),
                    neutralize_formula(employee.position),
                    employee.salary,
                ]
            )

        audit("export.directory_csv", rows=len(employees), filters={k: v for k, v in criteria.items() if v})
        db.session.commit()
        response = make_response(output.getvalue())
        response.headers["Content-Type"] = "text/csv; charset=utf-8"
        response.headers["Content-Disposition"] = "attachment; filename=peoplebase-employees.csv"
        # Salary data should not sit in shared or browser caches.
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.route("/employees/new", methods=["GET", "POST"])
    @admin_only
    def add_employee():
        if request.method == "POST":
            values, error = validate_employee(request.form)
            if not error:
                details, error = validate_employment(request.form)
            if error:
                flash(error, "error")
                return render_employee_form("add_employee.html", None, request.form), 400

            employee = Employee(**values, **details)
            employee.compensation_history.append(
                CompensationChange(
                    position=employee.position,
                    salary=employee.salary,
                    effective_date=employee.start_date or date.today(),
                    reason="Hired",
                )
            )
            db.session.add(employee)
            audit("employee.created", employee, **employee_snapshot(employee))
            db.session.commit()
            flash(f"Added {employee.name}.", "success")
            return redirect(url_for("employee_profile", employee_id=employee.id))

        return render_employee_form("add_employee.html", None, {})

    @app.get("/employees/<int:employee_id>")
    def employee_profile(employee_id):
        employee = db.get_or_404(Employee, employee_id)
        if not g.access.can_view_profile(employee):
            # The same answer as a record that does not exist, so ids cannot be probed.
            abort(404)
        return render_template("employee.html", employee=employee, today=date.today())

    @app.route("/employees/<int:employee_id>/edit", methods=["GET", "POST"])
    @admin_only
    def update_employee(employee_id):
        employee = db.get_or_404(Employee, employee_id)
        if request.method == "POST":
            values, error = validate_employee(request.form)
            if not error:
                details, error = validate_employment(request.form, employee)
            if not error and details["status"] == "terminated" and g.access.is_self(employee):
                error = "You cannot mark your own record as left. Ask another HR admin."
            if not error:
                change, error = validate_compensation_change(request.form, employee, values)
            if error:
                flash(error, "error")
                return render_employee_form(
                    "update_employee.html", employee, request.form
                ), 400

            before = employee_snapshot(employee)
            for field, value in {**values, **details}.items():
                setattr(employee, field, value)
            if change:
                employee.compensation_history.append(change)
                audit(
                    "compensation.changed",
                    employee,
                    position=[before["position"], change.position],
                    salary=[before["salary"], str(change.salary)],
                    effective_date=change.effective_date,
                    reason=change.reason,
                )
            after = employee_snapshot(employee)
            # Pay and title have their own entry above; this one covers the rest.
            changed = {
                field: [before[field], after[field]]
                for field in after
                if before[field] != after[field] and field not in {"position", "salary"}
            }
            if changed:
                audit("employee.updated", employee, **changed)
            if employee.status == "terminated":
                close_account(employee, unlink=False)
            db.session.commit()
            flash(f"Updated {employee.name}.", "success")
            return redirect(url_for("employee_profile", employee_id=employee.id))

        return render_employee_form("update_employee.html", employee, {})

    @app.post("/employees/<int:employee_id>/delete")
    @admin_only
    def delete_employee(employee_id):
        employee = db.get_or_404(Employee, employee_id)
        if g.access.is_self(employee):
            flash("You cannot delete your own record. Ask another HR admin.", "error")
            return redirect(url_for("employee_profile", employee_id=employee.id))
        employee_name = employee.name
        audit("employee.deleted", employee, **employee_snapshot(employee))
        close_account(employee, unlink=True)
        # SQLite does not enforce foreign keys by default, so clear references here.
        for report in employee.reports:
            report.manager = None
        db.session.execute(
            db.update(TimeOffRequest)
            .where(TimeOffRequest.decided_by_id == employee.id)
            .values(decided_by_id=None)
        )
        db.session.delete(employee)
        db.session.commit()
        flash(f"Deleted {employee_name}.", "success")
        return redirect(url_for("view_employees"))

    @app.get("/departments")
    @admin_only
    def view_departments():
        return render_template("departments.html", departments=department_headcounts(), values={})

    @app.post("/departments")
    @admin_only
    def add_department():
        name, error = validate_department_name(request.form)
        if error:
            flash(error, "error")
            return render_template(
                "departments.html", departments=department_headcounts(), values=request.form
            ), 400
        department = Department(name=name)
        db.session.add(department)
        audit("department.created", department)
        db.session.commit()
        flash(f"Added the {name} department.", "success")
        return redirect(url_for("view_departments"))

    @app.post("/departments/<int:department_id>/rename")
    @admin_only
    def rename_department(department_id):
        department = db.get_or_404(Department, department_id)
        name, error = validate_department_name(request.form, department)
        if error:
            flash(error, "error")
            return render_template(
                "departments.html",
                departments=department_headcounts(),
                values={},
                rename_id=department.id,
                rename_values=request.form,
            ), 400
        if name == department.name:
            flash(f"{name} already has that name.", "info")
            return redirect(url_for("view_departments"))
        old_name = department.name
        department.name = name
        audit("department.renamed", department, name=[old_name, name])
        db.session.commit()
        flash(f"Renamed {old_name} to {name}.", "success")
        return redirect(url_for("view_departments"))

    @app.post("/departments/<int:department_id>/delete")
    @admin_only
    def delete_department(department_id):
        department = db.get_or_404(Department, department_id)
        if department.employees:
            headcount = len(department.employees)
            flash(
                f"{department.name} still has {headcount} {'person' if headcount == 1 else 'people'}. "
                "Move them to another department first.",
                "error",
            )
            return redirect(url_for("view_departments"))
        audit("department.deleted", department)
        db.session.delete(department)
        db.session.commit()
        flash(f"Deleted the {department.name} department.", "success")
        return redirect(url_for("view_departments"))

    @app.get("/time-off")
    def view_time_off():
        status = request.args.get("status", "").strip()
        if status not in TIME_OFF_STATUSES:
            status = ""
        statement = db.select(TimeOffRequest).order_by(
            TimeOffRequest.start_date.desc(), TimeOffRequest.id.desc()
        )
        if status:
            statement = statement.where(TimeOffRequest.status == status)
        visible = g.access.visible_profile_ids()
        if visible is not None:
            statement = statement.where(TimeOffRequest.employee_id.in_(visible))
        return render_template(
            "time_off.html",
            requests=db.session.scalars(statement).all(),
            selected_status=status,
        )

    @app.route("/time-off/new", methods=["GET", "POST"])
    def request_time_off():
        if request.method == "POST":
            time_off, error = validate_time_off(request.form, g.access)
            if error:
                flash(error, "error")
                return render_time_off_form(request.form), 400
            db.session.add(time_off)
            audit("time_off.requested", time_off, **time_off_snapshot(time_off))
            db.session.commit()
            flash(
                f"Requested {time_off.days} {'day' if time_off.days == 1 else 'days'} "
                f"off for {time_off.employee.name}.",
                "success",
            )
            return redirect(url_for("view_time_off"))
        values = {}
        employee_id = request.args.get("employee", "")
        if g.access.is_admin and is_small_id(employee_id):
            values["employee_id"] = employee_id
        return render_time_off_form(values)

    @app.post("/time-off/<int:request_id>/decide")
    def decide_time_off(request_id):
        time_off = db.get_or_404(TimeOffRequest, request_id)
        if not g.access.can_view_profile(time_off.employee):
            abort(404)
        error = apply_time_off_decision(time_off, request.form, g.access)
        if error:
            flash(error, "error")
        else:
            audit(f"time_off.{time_off.status}", time_off, **time_off_snapshot(time_off))
            db.session.commit()
            flash(
                f"{time_off.status_label} {time_off.employee.name}'s time off "
                f"from {time_off.start_date.isoformat()}.",
                "success",
            )
        return redirect(url_for("view_time_off"))

    @app.get("/audit")
    @admin_only
    def view_audit():
        group = request.args.get("group", "").strip()
        if group not in AUDIT_GROUPS:
            group = ""
        search = request.args.get("q", "").strip()[:100]
        statement = db.select(AuditEvent).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        if group:
            statement = statement.where(AuditEvent.action.startswith(f"{group}.", autoescape=True))
        if search:
            statement = statement.where(
                db.or_(
                    AuditEvent.actor_label.icontains(search, autoescape=True),
                    AuditEvent.subject_label.icontains(search, autoescape=True),
                )
            )
        page = min(max(request.args.get("page", 1, type=int), 1), 10_000)
        pagination = db.paginate(statement, page=page, per_page=AUDIT_PER_PAGE, error_out=False)
        return render_template(
            "audit.html",
            pagination=pagination,
            groups=AUDIT_GROUPS,
            selected_group=group,
            search=search,
            page_args={"group": group or None, "q": search or None},
        )

    @app.cli.command("seed-demo")
    def seed_demo_command():
        """Load clearly fake demo data into an empty database."""
        require_schema()
        if db.session.scalar(db.select(db.func.count(Employee.id))):
            raise click.ClickException(
                "The database already has employees. seed-demo only fills an empty "
                "database, so it never mixes demo rows with real ones."
            )
        summary = seed_demo_data(date.today())
        click.echo(
            f"Added {summary['employees']} demo employees in {summary['departments']} "
            f"departments and {summary['time_off']} time-off requests."
        )
        click.echo(
            f"Created {summary['accounts']} demo accounts. They are fake, and they all share "
            "this password, generated for this run and not stored anywhere else:"
        )
        click.echo(f"  password: {summary['password']}")
        for role, email, name in summary["sign_ins"]:
            click.echo(f"  {ROLES[role]:<9} {email}  ({name})")
        click.echo("Every other demo person signs in as <first name>@peoplebase.example.")

    @app.cli.command("create-admin")
    @click.option("--email", prompt=True, help="Sign-in email for the new HR admin.")
    @click.password_option(help="At least 12 characters. Prompted for when left out.")
    def create_admin_command(email, password):
        """Create an HR admin account, for example the first one on a new install."""
        require_schema()
        email = normalize_email(email)
        if not EMAIL_PATTERN.fullmatch(email) or CONTROL_CHARACTERS.search(email):
            raise click.ClickException("Enter a valid email address.")
        if db.session.scalar(db.select(Account).where(Account.email == email)) is not None:
            raise click.ClickException(f"An account for {email} already exists.")
        error = password_problem(password, email)
        if error:
            raise click.ClickException(error)
        account = Account(
            email=email, role="admin", password_hash=hash_password(password), must_change_password=False
        )
        db.session.add(account)
        audit("account.created", account, role="admin", via="create-admin command")
        db.session.commit()
        click.echo(f"Created the HR admin account {email}.")

    return app


def require_schema():
    if not schema_ready():
        raise click.ClickException(
            "The database has no PeopleBase tables yet. Run: flask --app app db upgrade"
        )


def schema_ready():
    """True once the migrations have run against the configured database."""
    return inspect(db.engine).has_table("employee")


SUBJECT_TYPES = {Account: "account", Employee: "employee", Department: "department", TimeOffRequest: "time off"}


def audit(action, subject=None, label=None, actor=None, **details):
    """Add one entry to the audit log, in the same transaction as the change
    it describes: the caller's commit saves both or neither.

    actor defaults to the signed-in account. Outside a request (the CLI) or
    before sign-in, the entry says so instead.
    """
    if action not in AUDIT_ACTIONS:
        raise ValueError(f"Unknown audit action: {action}")
    if actor is None and has_request_context():
        actor = g.get("account")
    if actor is not None:
        actor_label = actor.email
    else:
        actor_label = "not signed in" if has_request_context() else "command line"
    subject_id = None
    if subject is not None:
        if subject.id is None:
            db.session.flush()
        subject_id = subject.id
        label = label or describe_subject(subject)
    db.session.add(
        AuditEvent(
            actor_account_id=actor.id if actor is not None else None,
            actor_label=actor_label,
            action=action,
            subject_type=SUBJECT_TYPES.get(type(subject), ""),
            subject_id=subject_id,
            subject_label=(label or "")[:254],
            details=json.dumps({key: value for key, value in details.items() if value is not None}, default=str),
        )
    )


def describe_subject(subject):
    if isinstance(subject, Account):
        return subject.email
    if isinstance(subject, TimeOffRequest):
        return subject.employee.name
    return subject.name


def employee_snapshot(employee):
    """The audited fields of an employee as plain text, for before and after."""
    return {
        "name": employee.name,
        "position": employee.position,
        "salary": str(employee.salary),
        "department": employee.department.name if employee.department else None,
        "manager": employee.manager.name if employee.manager else None,
        "status": employee.status,
        "start_date": employee.start_date.isoformat() if employee.start_date else None,
    }


def time_off_snapshot(time_off):
    return {"kind": time_off.kind, "first_day": time_off.start_date, "last_day": time_off.end_date}


def admin_only(view):
    """Limit a route to HR admins. Everyone else gets 403."""

    @wraps(view)
    def guarded(*args, **kwargs):
        if not g.access.is_admin:
            abort(403, description="This page is for HR admins.")
        return view(*args, **kwargs)

    return guarded


def start_session(account):
    """Begin a fresh session for account. Clearing first drops anything that
    was in the cookie before sign-in, including its CSRF token."""
    session.clear()
    session.permanent = True
    session["account_id"] = account.id
    session["nonce"] = account.session_nonce
    session["auth_at"] = int(time.time())


def session_is_current(account):
    """True when the cookie still matches the account: the account may sign in,
    its nonce has not been rotated and the session is not too old."""
    if not may_sign_in(account):
        return False
    nonce = str(session.get("nonce", ""))
    if not hmac.compare_digest(nonce.encode(), account.session_nonce.encode()):
        return False
    signed_in_at = session.get("auth_at")
    if not isinstance(signed_in_at, int):
        return False
    return 0 <= time.time() - signed_in_at <= SESSION_LIFETIME.total_seconds()


def may_sign_in(account):
    if account is None or not account.active:
        return False
    return account.employee is None or account.employee.status != "terminated"


def hash_password(password):
    return current_app.extensions["password_hasher"].hash(password)


def password_matches(account, password):
    """Check a password in the same time whether or not the account exists."""
    hasher = current_app.extensions["password_hasher"]
    stored = account.password_hash if account is not None else current_app.extensions["password_decoy"]
    try:
        matched = hasher.verify(stored, password[: PASSWORD_MAX_LENGTH + 1])
    except (VerificationError, InvalidHashError):
        matched = False
    return matched and account is not None and len(password) <= PASSWORD_MAX_LENGTH


def upgrade_password_hash(account, password):
    """Re-hash at sign-in when the stored hash uses older cost settings."""
    hasher = current_app.extensions["password_hasher"]
    if hasher.check_needs_rehash(account.password_hash):
        account.password_hash = hasher.hash(password)


def password_problem(password, email):
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"Use a password of at least {PASSWORD_MIN_LENGTH} characters."
    if len(password) > PASSWORD_MAX_LENGTH:
        return f"Use a password of at most {PASSWORD_MAX_LENGTH} characters."
    if password.strip().lower() == email:
        return "Your password cannot be your email address."
    return None


def validate_new_password(form, account):
    new_password = form.get("new_password", "")
    error = password_problem(new_password, account.email)
    if error:
        return None, error
    if new_password != form.get("confirm_password", ""):
        return None, "The two new passwords do not match."
    if password_matches(account, new_password):
        return None, "Choose a password you have not just been using."
    return new_password, None


def new_temporary_password():
    return secrets.token_urlsafe(12)


def normalize_email(value):
    return value.strip().lower()[:254]


def safe_next(target):
    """A local path to return to after sign-in, or None. Anything that could
    leave the site (a scheme, a host, a protocol-relative //) is dropped."""
    if not target or not target.startswith("/") or target.startswith("//"):
        return None
    if "\\" in target or CONTROL_CHARACTERS.search(target):
        return None
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        return None
    return target


def throttle_locked(key, now):
    row = db.session.get(LoginThrottle, key)
    return row is not None and row.locked_until is not None and row.locked_until > now


def record_failed_login(key, limit, now):
    """Count a failure for key and lock it once limit is reached in one window."""
    db.session.execute(
        db.delete(LoginThrottle).where(LoginThrottle.window_started_at < now - timedelta(days=1))
    )
    row = db.session.get(LoginThrottle, key)
    if row is None:
        row = LoginThrottle(key=key, failures=0, window_started_at=now)
        db.session.add(row)
    elif now - row.window_started_at > LOGIN_WINDOW:
        row.failures = 0
        row.window_started_at = now
        row.locked_until = None
    row.failures += 1
    if row.failures >= limit:
        row.locked_until = now + LOGIN_LOCK


def clear_throttle(key):
    row = db.session.get(LoginThrottle, key)
    if row is not None:
        db.session.delete(row)


def close_account(employee, unlink):
    """Stop the sign-in of someone who has left or whose record is going away."""
    account = employee.account
    if account is None:
        return
    account.active = False
    account.end_sessions()
    if unlink:
        account.employee = None


def render_accounts(values, issued=None):
    accounts = db.session.scalars(db.select(Account).order_by(Account.email)).all()
    linkable = db.session.scalars(
        db.select(Employee)
        .where(Employee.status != "terminated", ~Employee.account.has())
        .order_by(Employee.name, Employee.id)
    ).all()
    now = utcnow()
    locked = {account.id for account in accounts if throttle_locked(f"email:{account.email}", now)}
    return render_template(
        "accounts.html", accounts=accounts, employees=linkable, values=values, issued=issued, locked=locked
    )


def validate_account(form):
    email = normalize_email(form.get("email", ""))
    if not EMAIL_PATTERN.fullmatch(email) or CONTROL_CHARACTERS.search(email):
        return None, "Enter a valid email address."
    if db.session.scalar(db.select(Account).where(Account.email == email)) is not None:
        return None, f"An account for {email} already exists."
    role = form.get("role", "").strip()
    if role not in ROLES:
        return None, "Choose a valid role."
    employee, error = parse_id(form.get("employee_id"), Employee, "employee")
    if error:
        return None, error
    if employee is None and role != "admin":
        return None, "Employee and manager accounts must be linked to an employee record."
    if employee is not None:
        if employee.status == "terminated":
            return None, f"{employee.name} no longer works here."
        if employee.account is not None:
            return None, f"{employee.name} already has an account."
    return {"email": email, "role": role, "employee": employee}, None


def apply_role_change(account, role, actor):
    """Change an account's role. Nobody changes their own, so the admin making
    the change stays an admin and there is always at least one."""
    if role not in ROLES:
        return "Choose a valid role."
    if account.id == actor.id:
        return "You cannot change your own role. Ask another HR admin."
    if role != "admin" and account.employee is None:
        return "Link the account to an employee record before making it an employee or manager account."
    if role != account.role:
        account.role = role
        account.end_sessions()
    return None


def apply_active_change(account, active, actor):
    if account.id == actor.id:
        return "You cannot turn off your own account. Ask another HR admin."
    if active and account.employee is not None and account.employee.status == "terminated":
        return f"{account.employee.name} has left, so their account stays off."
    account.active = active
    if not active:
        account.end_sessions()
    return None


def render_personal_home(today):
    """The dashboard for employees and managers: their own time off, and for
    managers the requests waiting on them and who on their team is out."""
    employee = g.account.employee
    own_requests = []
    if employee is not None:
        own_requests = db.session.scalars(
            db.select(TimeOffRequest)
            .where(TimeOffRequest.employee_id == employee.id, TimeOffRequest.end_date >= today)
            .order_by(TimeOffRequest.start_date)
        ).all()
    waiting = []
    team_out = []
    reports = g.access.reports
    if reports:
        waiting = db.session.scalars(
            db.select(TimeOffRequest)
            .where(TimeOffRequest.status == "pending", TimeOffRequest.employee_id.in_(reports))
            .order_by(TimeOffRequest.start_date)
        ).all()
        team_out = db.session.scalars(
            db.select(TimeOffRequest)
            .where(
                TimeOffRequest.status == "approved",
                TimeOffRequest.employee_id.in_(reports),
                TimeOffRequest.start_date <= today,
                TimeOffRequest.end_date >= today,
            )
            .order_by(TimeOffRequest.end_date)
        ).all()
    return render_template(
        "home_personal.html",
        employee=employee,
        own_requests=own_requests,
        waiting=waiting,
        team_out=team_out,
        report_count=len(reports),
        today=today,
    )


def get_csrf_token():
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def render_employee_form(template, employee, values):
    managers = db.session.scalars(
        db.select(Employee)
        .where(Employee.status != "terminated")
        .order_by(Employee.name, Employee.id)
    ).all()
    if employee is not None:
        managers = [manager for manager in managers if manager.id != employee.id]
    return render_template(
        template,
        employee=employee,
        values=values,
        departments=all_departments(),
        managers=managers,
        today=date.today(),
    )


def all_departments():
    return db.session.scalars(db.select(Department).order_by(Department.name)).all()


def clean_text(form, field, label, max_length=100, required=True):
    value = form.get(field, "").strip()
    if required and not value:
        return None, f"{label} is required."
    if len(value) > max_length:
        return None, f"{label} must be {max_length} characters or fewer."
    if CONTROL_CHARACTERS.search(value):
        return None, f"{label} cannot contain tabs, line breaks or other control characters."
    return value, None


def parse_date(text_value, label, required=False):
    text_value = (text_value or "").strip()
    if not text_value:
        if required:
            return None, f"{label} is required."
        return None, None
    if not DATE_PATTERN.fullmatch(text_value):
        return None, f"{label} must be a date in YYYY-MM-DD format."
    try:
        value = date.fromisoformat(text_value)
    except ValueError:
        return None, f"{label} must be a real calendar date."
    if not MIN_DATE <= value <= MAX_DATE:
        return None, f"{label} must be between 1900 and 2100."
    return value, None


def is_small_id(text_value):
    """ASCII digits only, short enough that the database can compare it."""
    return text_value.isascii() and text_value.isdigit() and len(text_value) <= 9


def parse_id(text_value, model, label):
    text_value = (text_value or "").strip()
    if not text_value:
        return None, None
    if not is_small_id(text_value):
        return None, f"Choose a valid {label}."
    record = db.session.get(model, int(text_value))
    if record is None:
        return None, f"Choose a valid {label}."
    return record, None


def validate_employee(form):
    name = form.get("name", "").strip()
    position = form.get("position", "").strip()
    salary_text = form.get("salary", "").strip()

    if not name or not position or not salary_text:
        return None, "Name, position, and salary are required."
    if len(name) > 100 or len(position) > 100:
        return None, "Name and position must be 100 characters or fewer."
    if CONTROL_CHARACTERS.search(name) or CONTROL_CHARACTERS.search(position):
        return None, "Name and position cannot contain tabs, line breaks or other control characters."

    if not SALARY_PATTERN.fullmatch(salary_text):
        return None, "Salary must be a number."
    try:
        salary = Decimal(salary_text)
    except InvalidOperation:
        return None, "Salary must be a number."
    if not salary.is_finite():
        return None, "Salary must be a number."
    if salary < 0 or salary > MAX_SALARY:
        return None, "Salary must be between 0 and 999,999,999.99."

    # abs() turns a submitted "-0" into a plain zero after the range check.
    salary = abs(salary.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return {"name": name, "position": position, "salary": salary}, None


def validate_employment(form, employee=None):
    """Validate department, manager, status and start date. All are optional."""
    default_status = employee.status if employee is not None else "active"
    status = form.get("status", "").strip() or default_status
    if status not in EMPLOYMENT_STATUSES:
        return None, "Choose a valid employment status."

    start_date, error = parse_date(form.get("start_date"), "Start date")
    if error:
        return None, error

    department, error = parse_id(form.get("department_id"), Department, "department")
    if error:
        return None, error

    manager, error = parse_id(form.get("manager_id"), Employee, "manager")
    if error:
        return None, error
    if manager is not None:
        if employee is not None and manager.id == employee.id:
            return None, "An employee cannot be their own manager."
        if manager.status == "terminated":
            return None, "Choose a manager who still works here."
        if employee is not None and reports_up_to(manager, employee):
            return None, (
                f"{manager.name} already reports to {employee.name}, "
                "so they cannot also be their manager."
            )

    values = {
        "status": status,
        "start_date": start_date,
        "department": department,
        "manager": manager,
    }
    if employee is not None:
        # On edit, a field missing from the form keeps its value; a blank field clears it.
        optional_fields = (("start_date", "start_date"), ("department", "department_id"), ("manager", "manager_id"))
        for field, form_key in optional_fields:
            if form_key not in form:
                values[field] = getattr(employee, field)
    return values, None


def reports_up_to(person, ancestor):
    """True when ancestor appears anywhere in person's management chain."""
    seen = set()
    current = person.manager
    while current is not None and current.id not in seen:
        if current.id == ancestor.id:
            return True
        seen.add(current.id)
        current = current.manager
    return False


def validate_compensation_change(form, employee, values):
    """Return a CompensationChange when the title or salary changed, else None."""
    if values["position"] == employee.position and values["salary"] == employee.salary:
        return None, None
    effective_date, error = parse_date(form.get("effective_date"), "Effective date")
    if error:
        return None, error
    reason, error = clean_text(form, "reason", "Reason for change", required=False)
    if error:
        return None, error
    return CompensationChange(
        position=values["position"],
        salary=values["salary"],
        effective_date=effective_date or date.today(),
        reason=reason,
    ), None


def render_time_off_form(values):
    """HR picks who the request is for. Everyone else requests for themselves."""
    employees = []
    if g.access.is_admin:
        employees = db.session.scalars(
            db.select(Employee)
            .where(Employee.status != "terminated")
            .order_by(Employee.name, Employee.id)
        ).all()
    return render_template("request_time_off.html", employees=employees, values=values)


def validate_time_off(form, access):
    if access.is_admin:
        employee, error = parse_id(form.get("employee_id"), Employee, "employee")
        if error:
            return None, error
        if employee is None:
            return None, "Choose who the time off is for."
    else:
        # Never read employee_id from the form here: the request is always for
        # the signed-in person, whatever the browser sends.
        employee = access.account.employee
        if employee is None:
            return None, "Your account is not linked to an employee record. Ask HR to link it."
    if employee.status == "terminated":
        return None, f"{employee.name} no longer works here."

    kind = form.get("kind", "").strip()
    if kind not in TIME_OFF_KINDS:
        return None, "Choose a type of time off."

    start_date, error = parse_date(form.get("start_date"), "First day", required=True)
    if error:
        return None, error
    end_date, error = parse_date(form.get("end_date"), "Last day", required=True)
    if error:
        return None, error
    if end_date < start_date:
        return None, "The last day cannot be before the first day."
    if (end_date - start_date).days + 1 > MAX_TIME_OFF_DAYS:
        return None, f"A single request can cover at most {MAX_TIME_OFF_DAYS} days."

    note, error = clean_text(form, "note", "Note", max_length=200, required=False)
    if error:
        return None, error

    overlapping = db.session.scalar(
        db.select(TimeOffRequest).where(
            TimeOffRequest.employee_id == employee.id,
            TimeOffRequest.status.in_(["pending", "approved"]),
            TimeOffRequest.start_date <= end_date,
            TimeOffRequest.end_date >= start_date,
        )
    )
    if overlapping is not None:
        return None, (
            f"{employee.name} already has {overlapping.status_label.lower()} time off from "
            f"{overlapping.start_date.isoformat()} to {overlapping.end_date.isoformat()}."
        )

    return TimeOffRequest(
        employee=employee, kind=kind, start_date=start_date, end_date=end_date, note=note
    ), None


def apply_time_off_decision(time_off, form, access):
    """Approve or deny a pending request. Returns an error message or None.

    The decider is the signed-in account, never a form field. HR decides for
    anyone and a manager for their direct and indirect reports. Nobody decides
    their own request.
    """
    decision = TIME_OFF_DECISIONS.get(form.get("decision", "").strip())
    if decision is None:
        return "Choose approve or deny."
    if time_off.status != "pending":
        return f"This request was already {time_off.status_label.lower()}."
    if access.is_self(time_off.employee):
        return "People cannot decide their own time off."
    if not access.can_decide_time_off(time_off):
        return f"Only {time_off.employee.name}'s managers or HR can decide this request."

    time_off.status = decision
    time_off.decided_by = access.account.employee
    time_off.decided_at = utcnow()
    return None


def validate_department_name(form, department=None):
    """Trimmed, non-empty, at most 100 characters, no control characters, and
    unique ignoring case. When renaming, the department may keep its own name
    or change only its capitalization."""
    name, error = clean_text(form, "name", "Department name")
    if error:
        return None, error
    query = db.select(Department).where(db.func.lower(Department.name) == name.lower())
    if department is not None:
        query = query.where(Department.id != department.id)
    existing = db.session.scalar(query)
    if existing is not None:
        return None, f"A department named {existing.name} already exists."
    return name, None


def department_headcounts():
    rows = db.session.execute(
        db.select(Department, db.func.count(Employee.id))
        .outerjoin(
            Employee,
            db.and_(Employee.department_id == Department.id, Employee.status != "terminated"),
        )
        .group_by(Department.id)
        .order_by(Department.name)
    ).all()
    return [{"department": department, "headcount": count} for department, count in rows]


def directory_criteria(args, access):
    status = args.get("status", "").strip()
    department = args.get("department", "").strip()
    return {
        "search": args.get("q", "").strip(),
        "position": args.get("position", "").strip(),
        "department": department if is_small_id(department) else "",
        # Status is HR-only, so for everyone else the filter does not exist.
        "status": status if access.is_admin and status in EMPLOYMENT_STATUSES else "",
    }


def directory_filters(criteria, access):
    """The SQL filters for a directory query. The page, its totals and the CSV
    export all use this, so they always describe the same people. Only HR sees
    people who have left."""
    filters = employee_filters(**criteria)
    if not access.is_admin:
        filters.append(Employee.status != "terminated")
    return filters


def employee_filters(search="", position="", department="", status=""):
    filters = []
    if search:
        filters.append(
            db.or_(
                Employee.name.icontains(search, autoescape=True),
                Employee.position.icontains(search, autoescape=True),
            )
        )
    if position:
        filters.append(Employee.position == position)
    if department:
        filters.append(Employee.department_id == int(department))
    if status:
        filters.append(Employee.status == status)
    return filters


def summarize_employees(filters):
    count, total = db.session.execute(
        db.select(
            db.func.count(Employee.id),
            db.func.coalesce(db.func.sum(Employee.salary), 0),
        ).where(*filters)
    ).one()
    total_payroll = Decimal(str(total))
    average_salary = total_payroll / count if count else Decimal(0)
    return {
        "matching_count": count,
        "total_payroll": total_payroll,
        "average_salary": average_salary,
    }


# Demo people are named "<first> Example" so nobody mistakes them for real records.
DEMO_DEPARTMENTS = ["Engineering", "Design", "Operations", "Finance", "People"]
DEMO_STAFF = [
    # name, title, department, manager, salary, years and days since start, status
    ("Morgan Example", "Chief Operating Officer", "Operations", None, "210000", (8, -12), "active"),
    ("Riley Example", "Engineering Director", "Engineering", "Morgan Example", "185000", (6, -3), "active"),
    ("Jordan Example", "Senior Engineer", "Engineering", "Riley Example", "152000", (4, 40), "active"),
    ("Casey Example", "Engineer", "Engineering", "Riley Example", "128000", (2, -20), "active"),
    ("Taylor Example", "Engineer", "Engineering", "Riley Example", "121000", (1, 200), "on_leave"),
    ("Avery Example", "Design Lead", "Design", "Morgan Example", "148000", (5, 15), "active"),
    ("Quinn Example", "Product Designer", "Design", "Avery Example", "112000", (1, -9), "active"),
    ("Drew Example", "Operations Manager", "Operations", "Morgan Example", "118000", (3, 120), "active"),
    ("Skyler Example", "Support Specialist", "Operations", "Drew Example", "68000", (0, 75), "active"),
    ("Harper Example", "Finance Manager", "Finance", "Morgan Example", "134000", (7, 28), "active"),
    ("Rowan Example", "Accountant", "Finance", "Harper Example", "86000", (2, 160), "active"),
    ("Emerson Example", "People Partner", "People", "Morgan Example", "104000", (3, -1), "active"),
    ("Parker Example", "Recruiter", "People", "Emerson Example", "79000", (4, 300), "terminated"),
]


# The People Partner is the HR admin. Anyone with reports gets the manager role.
DEMO_ADMIN = "Emerson Example"
DEMO_SIGN_INS = [("admin", "Emerson Example"), ("manager", "Riley Example"), ("employee", "Casey Example")]


def seed_demo_data(today):
    """Create the demo organization relative to today. Expects an empty database."""
    departments = {name: Department(name=name) for name in DEMO_DEPARTMENTS}
    db.session.add_all(departments.values())
    people = {}
    for name, title, department, manager, salary, (years, days), status in DEMO_STAFF:
        start = anniversary_on(today, today.year - years) - timedelta(days=days)
        employee = Employee(
            name=name,
            position=title,
            salary=Decimal(salary),
            department=departments[department],
            manager=people.get(manager),
            status=status,
            start_date=start,
        )
        employee.compensation_history.append(
            CompensationChange(position=title, salary=Decimal(salary), effective_date=start, reason="Hired")
        )
        people[name] = employee
        db.session.add(employee)

    raise_on = anniversary_on(today, today.year - 1)
    jordan = people["Jordan Example"]
    jordan.compensation_history[0].position = "Engineer"
    jordan.compensation_history[0].salary = Decimal("134000")
    jordan.compensation_history.append(
        CompensationChange(
            position="Senior Engineer", salary=Decimal("152000"), effective_date=raise_on, reason="Promotion"
        )
    )

    requests = [
        ("Casey Example", "vacation", -1, 3, "approved", "Riley Example"),
        ("Quinn Example", "sick", 0, 0, "approved", "Avery Example"),
        ("Jordan Example", "vacation", 14, 18, "pending", None),
        ("Rowan Example", "personal", 6, 6, "pending", None),
        ("Skyler Example", "vacation", 30, 34, "denied", "Drew Example"),
    ]
    for name, kind, first, last, status, approver in requests:
        db.session.add(
            TimeOffRequest(
                employee=people[name],
                kind=kind,
                start_date=today + timedelta(days=first),
                end_date=today + timedelta(days=last),
                status=status,
                decided_by=people.get(approver),
                decided_at=utcnow() if approver else None,
            )
        )
    # One account per current demo person. The domain is reserved for examples
    # and the password is random per run, so nothing here works anywhere else.
    password = new_temporary_password()
    password_hash = hash_password(password)
    managers = {manager for _, _, _, manager, _, _, _ in DEMO_STAFF if manager}
    accounts = 0
    for name, employee in people.items():
        if employee.status == "terminated":
            continue
        role = "admin" if name == DEMO_ADMIN else ("manager" if name in managers else "employee")
        db.session.add(
            Account(
                email=demo_email(name),
                role=role,
                employee=employee,
                password_hash=password_hash,
                must_change_password=False,
            )
        )
        accounts += 1
    db.session.commit()
    return {
        "employees": len(people),
        "departments": len(departments),
        "time_off": len(requests),
        "accounts": accounts,
        "password": password,
        "sign_ins": [(role, demo_email(name), name) for role, name in DEMO_SIGN_INS],
    }


def demo_email(name):
    return f"{name.split()[0].lower()}@peoplebase.example"


def headcount_by_department(employees):
    """[(department name, headcount)] for current staff, largest first."""
    counts = {}
    for employee in employees:
        name = employee.department.name if employee.department else "No department"
        counts[name] = counts.get(name, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def anniversary_on(start, year):
    """The anniversary of start in year. Feb 29 starts are marked on Feb 28 in other years."""
    try:
        return start.replace(year=year)
    except ValueError:
        return date(year, 2, 28)


def upcoming_anniversaries(employees, today, within_days=30):
    """Work anniversaries (one year or more) from today through within_days ahead."""
    horizon = today + timedelta(days=within_days)
    upcoming = []
    for employee in employees:
        if employee.start_date is None:
            continue
        for year in (today.year, today.year + 1):
            anniversary = anniversary_on(employee.start_date, year)
            years = year - employee.start_date.year
            if years >= 1 and today <= anniversary <= horizon:
                upcoming.append({"employee": employee, "date": anniversary, "years": years})
                break
    return sorted(upcoming, key=lambda item: (item["date"], item["employee"].name))


def build_org_tree(employees):
    """Nest employees under their managers.

    Anyone whose manager is missing from the list (no manager, or a manager who
    has left) becomes a root. A reporting loop written straight to the database
    cannot recurse forever: each person is placed once, and anyone left over is
    added as a root.
    """
    by_id = {employee.id: employee for employee in employees}
    children = {employee.id: [] for employee in employees}
    roots = []
    for employee in employees:
        if employee.manager_id in by_id and employee.manager_id != employee.id:
            children[employee.manager_id].append(employee)
        else:
            roots.append(employee)

    placed = set()

    def node(employee):
        placed.add(employee.id)
        return {
            "employee": employee,
            "reports": [node(child) for child in children[employee.id] if child.id not in placed],
        }

    tree = [node(root) for root in roots]
    for employee in employees:
        if employee.id not in placed:
            tree.append(node(employee))
    return tree


def neutralize_formula(value):
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


if __name__ == "__main__":
    create_app().run(debug=os.environ.get("FLASK_DEBUG") == "1")
