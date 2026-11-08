import csv
import hmac
import math
import os
import re
import secrets
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from io import StringIO
from pathlib import Path

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
# Columns added to the original employee table. db.create_all() never alters an
# existing table, so upgrade_schema() adds any that an older database lacks.
EMPLOYEE_UPGRADE_COLUMNS = {
    "department_id": "INTEGER REFERENCES department (id)",
    "manager_id": "INTEGER REFERENCES employee (id)",
    "status": "VARCHAR(20) NOT NULL DEFAULT 'active'",
    "start_date": "DATE",
}


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
        employee_count = db.session.scalar(db.select(db.func.count(Employee.id)))
        return render_template("home.html", employee_count=employee_count)

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

    return {
        "status": status,
        "start_date": start_date,
        "department": department,
        "manager": manager,
    }, None


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


def validate_department_name(form):
    name, error = clean_text(form, "name", "Department name")
    if error:
        return None, error
    existing = db.session.scalar(
        db.select(Department).where(db.func.lower(Department.name) == name.lower())
    )
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


def neutralize_formula(value):
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


if __name__ == "__main__":
    create_app().run(debug=os.environ.get("FLASK_DEBUG") == "1")
