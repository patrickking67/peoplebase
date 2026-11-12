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

    # Time off

    def can_request_time_off_for(self, employee):
        return self.is_admin or self.is_self(employee)

    def can_decide_time_off(self, time_off):
        """HR decides for anyone and managers for their reports. Nobody decides
        their own request."""
        if self.employee_id is not None and time_off.employee_id == self.employee_id:
            return False
        return self.is_admin or time_off.employee_id in self.reports

    # Reports

    def can_view_reports(self):
        return self.is_admin or self.is_manager
