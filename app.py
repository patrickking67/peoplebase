import csv
import hmac
import math
import os
import re
import secrets
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from io import StringIO
from pathlib import Path

import click
from flask import (
    Flask,
    abort,
    flash,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text
from werkzeug.exceptions import HTTPException, InternalServerError

db = SQLAlchemy()

MAX_SALARY = Decimal("999999999.99")
DEFAULT_PER_PAGE = 25
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

EMPLOYMENT_STATUSES = {
    "active": "Active",
    "on_leave": "On leave",
    "terminated": "Terminated",
}
TIME_OFF_KINDS = {"vacation": "Vacation", "sick": "Sick leave", "personal": "Personal"}
TIME_OFF_STATUSES = {"pending": "Pending", "approved": "Approved", "denied": "Denied"}
TIME_OFF_DECISIONS = {"approve": "approved", "deny": "denied"}
MAX_TIME_OFF_DAYS = 60
# Columns added to the original employee table. db.create_all() never alters an
# existing table, so upgrade_schema() adds any that an older database lacks.
EMPLOYEE_UPGRADE_COLUMNS = {
    "department_id": "INTEGER REFERENCES department (id)",
    "manager_id": "INTEGER REFERENCES employee (id)",
    "status": "VARCHAR(20) NOT NULL DEFAULT 'active'",
    "start_date": "DATE",
}


def utcnow():
    return datetime.now(UTC).replace(tzinfo=None)


class Department(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)

    employees = db.relationship("Employee", back_populates="department")

    def __repr__(self):
        return f"<Department {self.name}>"


class Employee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    position = db.Column(db.String(100), nullable=False)
    salary = db.Column(db.Numeric(12, 2), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey("department.id"), nullable=True)
    manager_id = db.Column(db.Integer, db.ForeignKey("employee.id"), nullable=True)
    status = db.Column(db.String(20), nullable=False, default="active", server_default="active")
    start_date = db.Column(db.Date, nullable=True)

    department = db.relationship("Department", back_populates="employees")
    manager = db.relationship("Employee", remote_side=[id], back_populates="reports")
    reports = db.relationship("Employee", back_populates="manager", order_by="Employee.name")
    compensation_history = db.relationship(
        "CompensationChange",
        back_populates="employee",
        cascade="all, delete-orphan",
        order_by="(CompensationChange.effective_date.desc(), CompensationChange.id.desc())",
    )

    time_off_requests = db.relationship(
        "TimeOffRequest",
        back_populates="employee",
        foreign_keys="TimeOffRequest.employee_id",
        cascade="all, delete-orphan",
        order_by="TimeOffRequest.start_date.desc()",
    )

    @property
    def status_label(self):
        return EMPLOYMENT_STATUSES.get(self.status, self.status)

    def __repr__(self):
        return f"<Employee {self.name}>"


class CompensationChange(db.Model):
    """One row per change to an employee's title or salary, with the date it takes effect."""

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"), nullable=False)
    position = db.Column(db.String(100), nullable=False)
    salary = db.Column(db.Numeric(12, 2), nullable=False)
    effective_date = db.Column(db.Date, nullable=False)
    reason = db.Column(db.String(100), nullable=False, default="")
    recorded_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    employee = db.relationship("Employee", back_populates="compensation_history")


class TimeOffRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"), nullable=False)
    kind = db.Column(db.String(20), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    note = db.Column(db.String(200), nullable=False, default="")
    status = db.Column(db.String(20), nullable=False, default="pending")
    requested_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    decided_by_id = db.Column(db.Integer, db.ForeignKey("employee.id"), nullable=True)
    decided_at = db.Column(db.DateTime, nullable=True)

    employee = db.relationship(
        "Employee", foreign_keys=[employee_id], back_populates="time_off_requests"
    )
    decided_by = db.relationship("Employee", foreign_keys=[decided_by_id])

    @property
    def days(self):
        """Calendar days covered, counting both the first and last day."""
        return (self.end_date - self.start_date).days + 1

    @property
    def kind_label(self):
        return TIME_OFF_KINDS.get(self.kind, self.kind)

    @property
    def status_label(self):
        return TIME_OFF_STATUSES.get(self.status, self.status)

    def approver_choices(self):
        """Who may decide this request: the direct manager, or when there is no
        current manager, anyone else who still works here."""
        manager = self.employee.manager
        if manager is not None and manager.status != "terminated":
            return [manager]
        return db.session.scalars(
            db.select(Employee)
            .where(Employee.id != self.employee_id, Employee.status != "terminated")
            .order_by(Employee.name, Employee.id)
        ).all()


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
    app.jinja_env.globals["csrf_token"] = get_csrf_token
    app.jinja_env.globals["EMPLOYMENT_STATUSES"] = EMPLOYMENT_STATUSES
    app.jinja_env.globals["TIME_OFF_KINDS"] = TIME_OFF_KINDS
    app.jinja_env.globals["TIME_OFF_STATUSES"] = TIME_OFF_STATUSES

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

    @app.after_request
    def add_security_headers(response):
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response

    @app.errorhandler(HTTPException)
    def handle_http_error(error):
        return render_template("error.html", error=error), error.code

    @app.errorhandler(InternalServerError)
    def handle_server_error(error):
        # Never show exception details; the traceback goes to the server log.
        db.session.rollback()
        return render_template("error.html", error=InternalServerError()), 500

    @app.get("/")
    def home():
        today = date.today()
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
        criteria = directory_criteria(request.args)
        page = max(request.args.get("page", 1, type=int), 1)
        per_page = request.args.get("per_page", DEFAULT_PER_PAGE, type=int)
        per_page = min(max(per_page, 1), MAX_PER_PAGE)

        filters = employee_filters(**criteria)
        summary = summarize_employees(filters)
        # Clamp to the last page before querying. A page past the end shows the
        # last page, and a huge page number never reaches the database as an
        # OFFSET too large for it to store.
        last_page = max(math.ceil(summary["matching_count"] / per_page), 1)
        page = min(page, last_page)
        statement = db.select(Employee).where(*filters).order_by(Employee.name, Employee.id)
        pagination = db.paginate(statement, page=page, per_page=per_page, error_out=False)

        positions = db.session.scalars(
            db.select(Employee.position).distinct().order_by(Employee.position)
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
    def export_employees():
        criteria = directory_criteria(request.args)
        employees = db.session.scalars(
            db.select(Employee)
            .where(*employee_filters(**criteria))
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

        response = make_response(output.getvalue())
        response.headers["Content-Type"] = "text/csv; charset=utf-8"
        response.headers["Content-Disposition"] = "attachment; filename=peoplebase-employees.csv"
        # Salary data should not sit in shared or browser caches.
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.route("/employees/new", methods=["GET", "POST"])
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
            db.session.commit()
            flash(f"Added {employee.name}.", "success")
            return redirect(url_for("employee_profile", employee_id=employee.id))

        return render_employee_form("add_employee.html", None, {})

    @app.get("/employees/<int:employee_id>")
    def employee_profile(employee_id):
        employee = db.get_or_404(Employee, employee_id)
        return render_template("employee.html", employee=employee, today=date.today())

    @app.route("/employees/<int:employee_id>/edit", methods=["GET", "POST"])
    def update_employee(employee_id):
        employee = db.get_or_404(Employee, employee_id)
        if request.method == "POST":
            values, error = validate_employee(request.form)
            if not error:
                details, error = validate_employment(request.form, employee)
            if not error:
                change, error = validate_compensation_change(request.form, employee, values)
            if error:
                flash(error, "error")
                return render_employee_form(
                    "update_employee.html", employee, request.form
                ), 400

            for field, value in {**values, **details}.items():
                setattr(employee, field, value)
            if change:
                employee.compensation_history.append(change)
            db.session.commit()
            flash(f"Updated {employee.name}.", "success")
            return redirect(url_for("employee_profile", employee_id=employee.id))

        return render_employee_form("update_employee.html", employee, {})

    @app.post("/employees/<int:employee_id>/delete")
    def delete_employee(employee_id):
        employee = db.get_or_404(Employee, employee_id)
        employee_name = employee.name
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
    def view_departments():
        return render_template("departments.html", departments=department_headcounts(), values={})

    @app.post("/departments")
    def add_department():
        name, error = validate_department_name(request.form)
        if error:
            flash(error, "error")
            return render_template(
                "departments.html", departments=department_headcounts(), values=request.form
            ), 400
        db.session.add(Department(name=name))
        db.session.commit()
        flash(f"Added the {name} department.", "success")
        return redirect(url_for("view_departments"))

    @app.post("/departments/<int:department_id>/rename")
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
        db.session.commit()
        flash(f"Renamed {old_name} to {name}.", "success")
        return redirect(url_for("view_departments"))

    @app.post("/departments/<int:department_id>/delete")
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
        return render_template(
            "time_off.html",
            requests=db.session.scalars(statement).all(),
            selected_status=status,
        )

    @app.route("/time-off/new", methods=["GET", "POST"])
    def request_time_off():
        if request.method == "POST":
            time_off, error = validate_time_off(request.form)
            if error:
                flash(error, "error")
                return render_time_off_form(request.form), 400
            db.session.add(time_off)
            db.session.commit()
            flash(
                f"Requested {time_off.days} {'day' if time_off.days == 1 else 'days'} "
                f"off for {time_off.employee.name}.",
                "success",
            )
            return redirect(url_for("view_time_off"))
        values = {}
        employee_id = request.args.get("employee", "")
        if is_small_id(employee_id):
            values["employee_id"] = employee_id
        return render_time_off_form(values)

    @app.post("/time-off/<int:request_id>/decide")
    def decide_time_off(request_id):
        time_off = db.get_or_404(TimeOffRequest, request_id)
        error = apply_time_off_decision(time_off, request.form)
        if error:
            flash(error, "error")
        else:
            db.session.commit()
            flash(
                f"{time_off.status_label} {time_off.employee.name}'s time off "
                f"from {time_off.start_date.isoformat()}.",
                "success",
            )
        return redirect(url_for("view_time_off"))

    @app.cli.command("seed-demo")
    def seed_demo_command():
        """Load clearly fake demo data into an empty database."""
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

    with app.app_context():
        db.create_all()
        upgrade_schema()

    return app


def upgrade_schema():
    """Bring a database created by an older PeopleBase up to the current schema.

    New tables come from db.create_all(). This adds the columns that later
    versions put on the existing employee table. Every added column is nullable
    or has a default, so existing rows stay valid.
    """
    existing = {column["name"] for column in inspect(db.engine).get_columns("employee")}
    missing = [name for name in EMPLOYEE_UPGRADE_COLUMNS if name not in existing]
    if not missing:
        return
    with db.engine.begin() as connection:
        for name in missing:
            connection.execute(
                text(f"ALTER TABLE employee ADD COLUMN {name} {EMPLOYEE_UPGRADE_COLUMNS[name]}")
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
    employees = db.session.scalars(
        db.select(Employee)
        .where(Employee.status != "terminated")
        .order_by(Employee.name, Employee.id)
    ).all()
    return render_template("request_time_off.html", employees=employees, values=values)


def validate_time_off(form):
    employee, error = parse_id(form.get("employee_id"), Employee, "employee")
    if error:
        return None, error
    if employee is None:
        return None, "Choose who the time off is for."
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


def apply_time_off_decision(time_off, form):
    """Approve or deny a pending request. Returns an error message or None.

    PeopleBase has no login, so the approver is named on the form and checked
    against the reporting line: the requester's manager decides, or for someone
    with no manager, any other current employee. Nobody decides their own request.
    """
    decision = TIME_OFF_DECISIONS.get(form.get("decision", "").strip())
    if decision is None:
        return "Choose approve or deny."
    if time_off.status != "pending":
        return f"This request was already {time_off.status_label.lower()}."

    approver, error = parse_id(form.get("approver_id"), Employee, "approver")
    if error:
        return error
    if approver is None:
        return "Choose who is making this decision."
    if approver.id == time_off.employee_id:
        return "People cannot decide their own time off."
    if approver not in time_off.approver_choices():
        manager = time_off.employee.manager
        if manager is not None and manager.status != "terminated":
            return f"Only {manager.name}, {time_off.employee.name}'s manager, can decide this request."
        return f"{approver.name} cannot decide this request."

    time_off.status = decision
    time_off.decided_by = approver
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


def directory_criteria(args):
    status = args.get("status", "").strip()
    department = args.get("department", "").strip()
    return {
        "search": args.get("q", "").strip(),
        "position": args.get("position", "").strip(),
        "department": department if is_small_id(department) else "",
        "status": status if status in EMPLOYMENT_STATUSES else "",
    }


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
    db.session.commit()
    return {"employees": len(people), "departments": len(departments), "time_off": len(requests)}


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
