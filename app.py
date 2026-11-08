import csv
import hmac
import os
import secrets
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


db = SQLAlchemy()

MAX_SALARY = Decimal("999999999.99")
DEFAULT_PER_PAGE = 25
MAX_PER_PAGE = 100
CSRF_SESSION_KEY = "_csrf_token"
CSRF_FORM_FIELD = "csrf_token"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# Spreadsheet apps treat cells starting with these characters as formulas.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


class Employee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    position = db.Column(db.String(100), nullable=False)
    salary = db.Column(db.Numeric(12, 2), nullable=False)

    def __repr__(self):
        return f"<Employee {self.name}>"


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

    @app.get("/")
    def home():
        employee_count = db.session.scalar(db.select(db.func.count(Employee.id)))
        return render_template("home.html", employee_count=employee_count)

    @app.get("/employees")
    def view_employees():
        search = request.args.get("q", "").strip()
        position = request.args.get("position", "").strip()
        page = max(request.args.get("page", 1, type=int), 1)
        per_page = request.args.get("per_page", DEFAULT_PER_PAGE, type=int)
        per_page = min(max(per_page, 1), MAX_PER_PAGE)

        filters = employee_filters(search, position)
        statement = db.select(Employee).where(*filters).order_by(Employee.name, Employee.id)
        pagination = db.paginate(statement, page=page, per_page=per_page, error_out=False)
        if pagination.pages and page > pagination.pages:
            pagination = db.paginate(
                statement, page=pagination.pages, per_page=per_page, error_out=False
            )

        summary = summarize_employees(filters)
        positions = db.session.scalars(
            db.select(Employee.position).distinct().order_by(Employee.position)
        ).all()
        return render_template(
            "view_employees.html",
            employees=pagination.items,
            pagination=pagination,
            page_args={
                "q": search or None,
                "position": position or None,
                "per_page": per_page if per_page != DEFAULT_PER_PAGE else None,
            },
            positions=positions,
            search=search,
            selected_position=position,
            **summary,
        )

    @app.get("/employees.csv")
    def export_employees():
        search = request.args.get("q", "").strip()
        position = request.args.get("position", "").strip()
        employees = db.session.scalars(
            filtered_employee_query(search, position).order_by(Employee.name, Employee.id)
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
        return response

    @app.route("/employees/new", methods=["GET", "POST"])
    def add_employee():
        if request.method == "POST":
            values, error = validate_employee(request.form)
            if error:
                flash(error, "error")
                return render_template("add_employee.html", values=request.form), 400

            db.session.add(Employee(**values))
            db.session.commit()
            flash(f"Added {values['name']}.", "success")
            return redirect(url_for("view_employees"))

        return render_template("add_employee.html", values={})

    @app.route("/employees/<int:employee_id>/edit", methods=["GET", "POST"])
    def update_employee(employee_id):
        employee = db.get_or_404(Employee, employee_id)
        if request.method == "POST":
            values, error = validate_employee(request.form)
            if error:
                flash(error, "error")
                return render_template(
                    "update_employee.html", employee=employee, values=request.form
                ), 400

            employee.name = values["name"]
            employee.position = values["position"]
            employee.salary = values["salary"]
            db.session.commit()
            flash(f"Updated {employee.name}.", "success")
            return redirect(url_for("view_employees"))

        return render_template("update_employee.html", employee=employee, values={})

    @app.post("/employees/<int:employee_id>/delete")
    def delete_employee(employee_id):
        employee = db.get_or_404(Employee, employee_id)
        employee_name = employee.name
        db.session.delete(employee)
        db.session.commit()
        flash(f"Deleted {employee_name}.", "success")
        return redirect(url_for("view_employees"))

    with app.app_context():
        db.create_all()

    return app


def get_csrf_token():
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def validate_employee(form):
    name = form.get("name", "").strip()
    position = form.get("position", "").strip()
    salary_text = form.get("salary", "").strip()

    if not name or not position or not salary_text:
        return None, "Name, position, and salary are required."
    if len(name) > 100 or len(position) > 100:
        return None, "Name and position must be 100 characters or fewer."

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


def employee_filters(search, position):
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
    return filters


def filtered_employee_query(search, position):
    return db.select(Employee).where(*employee_filters(search, position))


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
