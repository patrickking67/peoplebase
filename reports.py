"""Headcount and attrition, worked out from start dates and last days.

Pure functions over any objects with start_date, end_date, status and
department, so the arithmetic is tested without a database. The caller
decides which people to pass in; that is where a manager's view is cut down
to their own reporting tree.

Definitions:
- Someone is on staff from their start date through their last day.
- A month's starting headcount is everyone on staff the day before it begins.
- Hires start in the month and leavers have their last day in it, so
  ending = starting + hires - leavers always holds.
- Attrition is leavers divided by average headcount over the period.
- Someone marked as left with no last day on record cannot be placed in any
  month. They are left out and counted separately so the gap is visible.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal


@dataclass(frozen=True)
class MonthRow:
    first_day: date
    starting: int
    hires: int
    leavers: int

    @property
    def ending(self):
        return self.starting + self.hires - self.leavers

    @property
    def label(self):
        return self.first_day.strftime("%Y-%m")


@dataclass(frozen=True)
class HeadcountReport:
    months: list
    departments: list  # (name, current headcount, hires, leavers), largest first
    undated_leavers: int

    @property
    def headcount(self):
        return self.months[-1].ending if self.months else 0

    @property
    def hires(self):
        return sum(month.hires for month in self.months)

    @property
    def leavers(self):
        return sum(month.leavers for month in self.months)

    @property
    def average_headcount(self):
        if not self.months:
            return Decimal(0)
        total = sum(Decimal(month.starting + month.ending) / 2 for month in self.months)
        return total / len(self.months)

    @property
    def attrition_rate(self):
        """Leavers as a percentage of average headcount, or None with nobody on staff."""
        average = self.average_headcount
        if average == 0:
            return None
        return (Decimal(self.leavers) * 100 / average).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def month_starts(today, count):
    """The first day of each of the last count months, oldest first, ending with today's month."""
    year, month = today.year, today.month
    starts = []
    for _ in range(count):
        starts.append(date(year, month, 1))
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return starts[::-1]


def last_day_of_month(first_day):
    if first_day.month == 12:
        following = date(first_day.year + 1, 1, 1)
    else:
        following = date(first_day.year, first_day.month + 1, 1)
    return following - timedelta(days=1)


def last_day(person):
    """The day someone left, or None while they are on staff."""
    return person.end_date if person.status == "terminated" else None


def headcount_report(people, today, months=12):
    people = list(people)
    undated = [person for person in people if person.status == "terminated" and person.end_date is None]
    dated = [person for person in people if person not in undated]

    rows = []
    for first in month_starts(today, months):
        # The current month stops at today: a start date next week is not a hire yet.
        last = min(last_day_of_month(first), today)
        starting = sum(
            1
            for person in dated
            if (person.start_date is None or person.start_date < first)
            and (last_day(person) is None or last_day(person) >= first)
        )
        hires = sum(1 for person in dated if person.start_date is not None and first <= person.start_date <= last)
        leavers = sum(1 for person in dated if last_day(person) is not None and first <= last_day(person) <= last)
        rows.append(MonthRow(first_day=first, starting=starting, hires=hires, leavers=leavers))

    period_start = rows[0].first_day
    by_department = {}
    for person in dated:
        name = person.department.name if person.department else "No department"
        current, hires, leavers = by_department.get(name, (0, 0, 0))
        left = last_day(person)
        started = person.start_date
        if (started is None or started <= today) and (left is None or left > today):
            current += 1
        if started is not None and period_start <= started <= today:
            hires += 1
        if left is not None and period_start <= left <= today:
            leavers += 1
        by_department[name] = (current, hires, leavers)
    departments = sorted(
        ((name, *counts) for name, counts in by_department.items() if any(counts)),
        key=lambda row: (-row[1], row[0]),
    )
    return HeadcountReport(months=rows, departments=departments, undated_leavers=len(undated))
