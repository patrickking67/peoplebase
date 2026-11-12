"""Database models and the constants that describe them.

Kept apart from app.py so the policy and reporting modules can use the models
without importing the Flask routes.
"""

import secrets
from datetime import UTC, datetime

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

EMPLOYMENT_STATUSES = {
    "active": "Active",
    "on_leave": "On leave",
    "terminated": "Terminated",
}
TIME_OFF_KINDS = {"vacation": "Vacation", "sick": "Sick leave", "personal": "Personal"}
TIME_OFF_STATUSES = {"pending": "Pending", "approved": "Approved", "denied": "Denied"}
TIME_OFF_DECISIONS = {"approve": "approved", "deny": "denied"}


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


ROLES = {"employee": "Employee", "manager": "Manager", "admin": "HR admin"}


def new_session_nonce():
    return secrets.token_urlsafe(16)


class Account(db.Model):
    """A sign-in. Linked to at most one employee; HR admins may stand alone."""

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(254), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="employee")
    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"), nullable=True, unique=True)
    active = db.Column(db.Boolean, nullable=False, default=True)
    # True while the password is one HR or the CLI handed out.
    must_change_password = db.Column(db.Boolean, nullable=False, default=True)
    # Copied into the session cookie at sign-in. Rotating it signs the account
    # out everywhere, which a signed client-side cookie cannot do on its own.
    session_nonce = db.Column(db.String(32), nullable=False, default=new_session_nonce)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    last_login_at = db.Column(db.DateTime, nullable=True)

    employee = db.relationship("Employee", backref=db.backref("account", uselist=False))

    @property
    def role_label(self):
        return ROLES.get(self.role, self.role)

    @property
    def display_name(self):
        return self.employee.name if self.employee else self.email

    def end_sessions(self):
        self.session_nonce = new_session_nonce()

    def __repr__(self):
        return f"<Account {self.email}>"


class LoginThrottle(db.Model):
    """Failed sign-ins per key. A key is "email:<address>" or "ip:<address>",
    so guessing is slowed the same way whether or not the account exists."""

    key = db.Column(db.String(300), primary_key=True)
    failures = db.Column(db.Integer, nullable=False, default=0)
    window_started_at = db.Column(db.DateTime, nullable=False)
    locked_until = db.Column(db.DateTime, nullable=True)
