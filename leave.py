"""Leave allowances and balances.

Pure date arithmetic with no database or Flask imports, so every rule here can
be tested with plain values.

The rules:
- Balances run per calendar year and count working days, Monday to Friday.
- Vacation (20 days a year) and sick leave (10) accrue in twelve equal parts,
  one at the start of each month the person is employed.
- Personal days (3) are granted in full at the start of the year.
- Someone who joins part-way through a year gets the months from their start
  month onwards.
- Approved time off is "used". Pending requests are held against the balance
  so the same days cannot be promised twice.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal


@dataclass(frozen=True)
class Allowance:
    days_per_year: int
    accrues_monthly: bool


LEAVE_ALLOWANCES = {
    "vacation": Allowance(days_per_year=20, accrues_monthly=True),
    "sick": Allowance(days_per_year=10, accrues_monthly=True),
    "personal": Allowance(days_per_year=3, accrues_monthly=False),
}


@dataclass(frozen=True)
class LeaveBalance:
    kind: str
    accrues_monthly: bool
    entitlement: Decimal  # what the whole year is worth to this person
    accrued: Decimal  # how much of it has been earned by the as-of date
    used: Decimal  # approved working days in the year
    pending: Decimal  # requested and not yet decided

    @property
    def available(self):
        """What is left to book. Negative when requests run ahead of accrual."""
        return self.accrued - self.used - self.pending


def workdays(start, end):
    """Monday-to-Friday days from start to end, counting both ends."""
    if end < start:
        return 0
    weeks, extra = divmod((end - start).days + 1, 7)
    count = weeks * 5
    for offset in range(extra):
        if (start.weekday() + offset) % 7 < 5:
            count += 1
    return count


def workdays_in_year(start, end, year):
    """The part of a request that falls inside one calendar year."""
    return workdays(max(start, date(year, 1, 1)), min(end, date(year, 12, 31)))


def months_employed(year, start_date, through_month):
    """How many months of year, up to and including through_month, count for
    accrual. A month counts from the one the person started in."""
    first_month = 1
    if start_date is not None:
        if start_date.year > year:
            return 0
        if start_date.year == year:
            first_month = start_date.month
    return max(0, through_month - first_month + 1)


def round_days(value):
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def leave_balance(kind, year, as_of, start_date, requests):
    """The balance for one kind of leave in one year, as it stands on as_of.

    requests is any iterable of objects with kind, status, start_date and
    end_date. Other kinds and denied requests are ignored.
    """
    allowance = LEAVE_ALLOWANCES[kind]
    per_month = Decimal(allowance.days_per_year) / 12
    if as_of.year > year:
        through_month = 12
    elif as_of.year == year:
        through_month = as_of.month
    else:
        through_month = 0
    earned_months = months_employed(year, start_date, through_month)
    year_months = months_employed(year, start_date, 12)
    if allowance.accrues_monthly:
        accrued = per_month * earned_months
    else:
        accrued = per_month * year_months if earned_months else Decimal(0)

    totals = {"approved": 0, "pending": 0}
    for request in requests:
        if request.kind == kind and request.status in totals:
            totals[request.status] += workdays_in_year(request.start_date, request.end_date, year)

    return LeaveBalance(
        kind=kind,
        accrues_monthly=allowance.accrues_monthly,
        entitlement=round_days(per_month * year_months),
        accrued=round_days(accrued),
        used=Decimal(totals["approved"]),
        pending=Decimal(totals["pending"]),
    )


def leave_balances(year, as_of, start_date, requests):
    requests = list(requests)
    return [leave_balance(kind, year, as_of, start_date, requests) for kind in LEAVE_ALLOWANCES]


def request_shortfall(request, today, start_date, requests):
    """Working days by which request runs past what will have accrued by its
    first day, or zero. The request itself must be among requests.

    Accrual is read on the first day of the leave, not today, so booking
    December leave in November counts December's share. A request that crosses
    New Year is checked against the year it starts in.
    """
    as_of = max(today, request.start_date)
    balance = leave_balance(request.kind, request.start_date.year, as_of, start_date, requests)
    return max(Decimal(0), -balance.available)
