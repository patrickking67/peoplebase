"""Who may see and change what.

Every route asks this module instead of checking roles itself, so the whole
permission model is the one class below. A role grants a capability; the
reporting line sets how far it reaches. See docs/security.md for the table.
"""

from models import Employee, db


def reporting_tree(root_id):
    """Ids of everyone who reports to root_id, directly or through others.

    One query for the (id, manager_id) pairs, then a walk in memory. Each id is
    visited once, so a reporting loop written straight to the database cannot
    spin forever, and the root is never in its own tree.
    """
    if root_id is None:
        return frozenset()
    reports = {}
    for employee_id, manager_id in db.session.execute(db.select(Employee.id, Employee.manager_id)):
        reports.setdefault(manager_id, []).append(employee_id)
    found = set()
    queue = list(reports.get(root_id, ()))
    while queue:
        current = queue.pop()
        if current in found or current == root_id:
            continue
        found.add(current)
        queue.extend(reports.get(current, ()))
    return frozenset(found)


class Access:
    """The permissions of one signed-in account, worked out once per request."""

    def __init__(self, account):
        self.account = account
        self.role = account.role if account else None
        self.employee_id = account.employee_id if account else None
        self._reports = None

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def is_manager(self):
        return self.role == "manager"

    @property
    def reports(self):
        """Direct and indirect reports. Only the manager role has a reach."""
        if self._reports is None:
            self._reports = reporting_tree(self.employee_id) if self.is_manager else frozenset()
        return self._reports

    def is_self(self, employee):
        return self.employee_id is not None and employee.id == self.employee_id

    def manages(self, employee):
        return employee.id in self.reports

    # People

    def can_manage_people(self):
        """Add, edit and delete records, departments, accounts and policies."""
        return self.is_admin

    def can_view_profile(self, employee):
        return self.is_admin or self.is_self(employee) or self.manages(employee)

    def can_view_pay(self, employee):
        """Salary and compensation history: HR and the employee, not their manager."""
        return self.is_admin or self.is_self(employee)

    def visible_profile_ids(self):
        """None means everyone; otherwise the ids whose profiles are in reach."""
        if self.is_admin:
            return None
        own = {self.employee_id} if self.employee_id is not None else set()
        return frozenset(own | self.reports)

    def can_update_checklist(self, employee):
        """Tick onboarding and offboarding tasks: HR, or a manager for their reports."""
        return self.is_admin or self.manages(employee)

    def can_update_task(self, task, done):
        """HR and the managers above a person may tick or reopen any of their
        tasks. A person may tick, never reopen, a task that is theirs to do."""
        employee = task.employee
        if self.can_update_checklist(employee):
            return True
        return self.is_self(employee) and task.owner == "employee" and done

    # Time off

    def can_request_time_off_for(self, employee):
        return self.is_admin or self.is_self(employee)

    def can_decide_time_off(self, time_off):
        """HR decides for anyone and managers for their reports. Nobody decides
        their own request."""
        if self.employee_id is not None and time_off.employee_id == self.employee_id:
            return False
        return self.is_admin or time_off.employee_id in self.reports

    def team_calendar_ids(self):
        """Ids whose time off shows on the team calendar. None means everyone.

        HR sees everyone. Anyone else sees themselves, the people who report to
        them, their own manager and the other people under that manager.
        """
        if self.is_admin:
            return None
        ids = set(self.reports)
        if self.employee_id is not None:
            ids.add(self.employee_id)
            manager_id = db.session.scalar(db.select(Employee.manager_id).where(Employee.id == self.employee_id))
            if manager_id is not None:
                ids.add(manager_id)
                ids.update(db.session.scalars(db.select(Employee.id).where(Employee.manager_id == manager_id)))
        return frozenset(ids)

    def can_see_leave_details(self, employee):
        """Kind and pending requests: the person, their managers and HR. Colleagues only see who is out."""
        return self.can_view_profile(employee)

    # Documents

    def can_read_document(self, document):
        """HR reads everything. The person reads what is shared with them, and
        their managers (direct or above) read only what is shared with managers."""
        if self.is_admin:
            return True
        if self.is_self(document.employee):
            return document.audience in {"employee", "manager"}
        return document.audience == "manager" and self.manages(document.employee)

    def can_upload_document(self, employee):
        """HR for anyone, and a person for their own record."""
        return self.is_admin or self.is_self(employee)

    def can_delete_document(self, document):
        return self.is_admin

    # Performance reviews

    def is_reviewer_of(self, review):
        """The direct manager, and only while their account has the manager role."""
        return self.is_manager and review.employee.manager_id == self.employee_id and self.employee_id is not None

    def can_view_review(self, review):
        """HR, the person reviewed and their direct manager. Managers further up
        the line do not read reviews."""
        return self.is_admin or self.is_self(review.employee) or self.is_reviewer_of(review)

    def can_write_self_review(self, review):
        return self.is_self(review.employee) and review.cycle.is_open and not review.self_done

    def can_write_manager_review(self, review):
        """Only the direct manager, once the person has submitted their own, in an open cycle."""
        return self.is_reviewer_of(review) and review.cycle.is_open and review.self_done and not review.manager_done

    def can_see_self_review(self, review):
        """What the person wrote: them, HR, and their direct manager once it is submitted."""
        if not review.self_done:
            return False
        return self.is_admin or self.is_self(review.employee) or self.is_reviewer_of(review)

    def can_see_manager_review(self, review):
        """The manager's write-up: HR and its author always; the person only after HR closes the cycle."""
        if not review.manager_done:
            return False
        if self.is_admin or self.is_reviewer_of(review):
            return True
        return self.is_self(review.employee) and not review.cycle.is_open

    # Reports

    def can_view_reports(self):
        return self.is_admin or self.is_manager
