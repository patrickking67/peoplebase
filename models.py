"""Database models and the constants that describe them.

Kept apart from app.py so the policy and reporting modules can use the models
without importing the Flask routes.
"""

import json
import secrets
from datetime import UTC, datetime

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event

db = SQLAlchemy()

EMPLOYMENT_STATUSES = {
    "active": "Active",
    "on_leave": "On leave",
    "terminated": "Terminated",
}
CHECKLIST_KINDS = {"onboarding": "Onboarding", "offboarding": "Offboarding"}
# The standard tasks. A new hire gets the first list, someone leaving the second.
CHECKLIST_TEMPLATES = {
    "onboarding": [
        "Signed contract on file",
        "Payroll and tax details collected",
        "Laptop and accounts ready",
        "PeopleBase account created",
        "First-week plan agreed with their manager",
    ],
    "offboarding": [
        "Last working day confirmed in writing",
        "Handover notes written",
        "Laptop and badge returned",
        "Accounts and access removed",
        "Final pay and unused leave settled",
    ],
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
    # Last working day. Set when the status becomes "terminated".
    end_date = db.Column(db.Date, nullable=True)

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

    checklist_tasks = db.relationship(
        "ChecklistTask",
        back_populates="employee",
        cascade="all, delete-orphan",
        order_by="(ChecklistTask.kind.desc(), ChecklistTask.position)",
    )

    acknowledgements = db.relationship(
        "Acknowledgement", back_populates="employee", cascade="all, delete-orphan"
    )

    @property
    def status_label(self):
        return EMPLOYMENT_STATUSES.get(self.status, self.status)

    def checklist(self, kind):
        return [task for task in self.checklist_tasks if task.kind == kind]

    def start_checklist(self, kind):
        """Give the employee the standard tasks for kind, unless they have them."""
        if not self.checklist(kind):
            for position, title in enumerate(CHECKLIST_TEMPLATES[kind], start=1):
                self.checklist_tasks.append(ChecklistTask(kind=kind, title=title, position=position))

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


class ChecklistTask(db.Model):
    """One onboarding or offboarding task for one employee."""

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"), nullable=False, index=True)
    kind = db.Column(db.String(20), nullable=False)
    title = db.Column(db.String(120), nullable=False)
    position = db.Column(db.Integer, nullable=False)
    completed_at = db.Column(db.DateTime, nullable=True)
    # The email of whoever ticked it, kept as text so it survives account changes.
    completed_by = db.Column(db.String(254), nullable=True)

    employee = db.relationship("Employee", back_populates="checklist_tasks")

    @property
    def done(self):
        return self.completed_at is not None

    @property
    def kind_label(self):
        return CHECKLIST_KINDS.get(self.kind, self.kind)


def checklist_progress(tasks):
    """(done, total, label) for a list of tasks."""
    total = len(tasks)
    done = sum(1 for task in tasks if task.done)
    if total == 0:
        label = "Not started"
    elif done == total:
        label = "Complete"
    elif done == 0:
        label = "Not started"
    else:
        label = "In progress"
    return done, total, label


class Policy(db.Model):
    """A policy people must acknowledge. The text never changes after it is
    published: a revision is a new row that replaces this one, so every
    acknowledgement points at the exact words that were agreed to."""

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    body = db.Column(db.Text, nullable=False)
    published_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    published_by = db.Column(db.String(254), nullable=False)
    retired_at = db.Column(db.DateTime, nullable=True)
    replaces_id = db.Column(db.Integer, db.ForeignKey("policy.id"), nullable=True)

    replaces = db.relationship("Policy", remote_side=[id])
    acknowledgements = db.relationship("Acknowledgement", back_populates="policy", cascade="all, delete-orphan")

    @property
    def current(self):
        return self.retired_at is None


class Acknowledgement(db.Model):
    """One employee confirming they read one policy."""

    __table_args__ = (db.UniqueConstraint("policy_id", "employee_id", name="uq_acknowledgement_policy_employee"),)

    id = db.Column(db.Integer, primary_key=True)
    policy_id = db.Column(db.Integer, db.ForeignKey("policy.id"), nullable=False)
    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"), nullable=False, index=True)
    acknowledged_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    policy = db.relationship("Policy", back_populates="acknowledgements")
    employee = db.relationship("Employee", back_populates="acknowledgements")


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


# Every action the audit log can hold, with the wording HR sees.
AUDIT_ACTIONS = {
    "auth.signed_in": "Signed in",
    "auth.sign_in_failed": "Failed sign-in",
    "auth.signed_out": "Signed out",
    "auth.password_changed": "Changed own password",
    "account.created": "Created account",
    "account.role_changed": "Changed role",
    "account.turned_off": "Turned account off",
    "account.turned_on": "Turned account on",
    "account.password_reset": "Reset password",
    "employee.created": "Added employee",
    "employee.updated": "Changed employee record",
    "employee.deleted": "Deleted employee",
    "compensation.changed": "Changed pay or title",
    "department.created": "Added department",
    "department.renamed": "Renamed department",
    "department.deleted": "Deleted department",
    "time_off.requested": "Requested time off",
    "time_off.approved": "Approved time off",
    "time_off.denied": "Denied time off",
    "checklist.completed": "Completed checklist task",
    "checklist.reopened": "Reopened checklist task",
    "policy.published": "Published policy",
    "policy.revised": "Revised policy",
    "policy.retired": "Retired policy",
    "policy.acknowledged": "Acknowledged policy",
    "export.directory_csv": "Exported directory CSV",
}
AUDIT_GROUPS = {
    "auth": "Sign-in",
    "account": "Accounts and roles",
    "employee": "Employee records",
    "compensation": "Pay",
    "department": "Departments",
    "time_off": "Time off",
    "checklist": "Onboarding and offboarding",
    "policy": "Policies",
    "export": "Exports",
}


class AuditLogError(RuntimeError):
    """Raised when code tries to change or remove an audit entry."""


class AuditEvent(db.Model):
    """One line of the audit log: who did what to whom, and when.

    Rows are only ever inserted. Names are copied in as text and ids are plain
    integers rather than foreign keys, so an entry still reads correctly after
    the person or record it mentions is renamed or deleted.
    """

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    actor_account_id = db.Column(db.Integer, nullable=True)
    actor_label = db.Column(db.String(254), nullable=False)
    action = db.Column(db.String(40), nullable=False, index=True)
    subject_type = db.Column(db.String(30), nullable=False, default="")
    subject_id = db.Column(db.Integer, nullable=True)
    subject_label = db.Column(db.String(254), nullable=False, default="")
    # JSON object of the specifics, for example {"salary": ["128000.00", "134000.00"]}.
    details = db.Column(db.Text, nullable=False, default="{}")

    @property
    def action_label(self):
        return AUDIT_ACTIONS.get(self.action, self.action)

    @property
    def detail_items(self):
        return sorted(json.loads(self.details).items())


@event.listens_for(AuditEvent, "before_update")
@event.listens_for(AuditEvent, "before_delete")
def refuse_audit_change(mapper, connection, target):
    raise AuditLogError("Audit entries cannot be changed or deleted.")


@event.listens_for(db.session, "do_orm_execute")
def refuse_bulk_audit_change(state):
    """Catch UPDATE and DELETE statements that skip the per-row hooks above."""
    if not (state.is_update or state.is_delete):
        return
    # ORM statements carry an annotated copy of the table, so compare by name.
    table = getattr(state.statement, "table", None)
    if getattr(table, "name", None) == AuditEvent.__tablename__:
        raise AuditLogError("Audit entries cannot be changed or deleted.")
