"""Frozen copies of the schemas PeopleBase shipped before it had migrations.

These are deliberately not the live models: they must keep describing the old
databases even as models.py moves on, so the upgrade tests keep meaning
"a real database from that version".
"""

import sqlalchemy as sa


def first_release():
    """The first release: one employee table, created by db.create_all()."""
    metadata = sa.MetaData()
    sa.Table(
        "employee",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("position", sa.String(100), nullable=False),
        sa.Column("salary", sa.Numeric(12, 2), nullable=False),
    )
    return metadata


def before_migrations():
    """The four-table schema that db.create_all() produced just before the
    project switched to Alembic."""
    metadata = sa.MetaData()
    sa.Table(
        "department",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
    )
    sa.Table(
        "employee",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("position", sa.String(100), nullable=False),
        sa.Column("salary", sa.Numeric(12, 2), nullable=False),
        sa.Column("department_id", sa.Integer, sa.ForeignKey("department.id"), nullable=True),
        sa.Column("manager_id", sa.Integer, sa.ForeignKey("employee.id"), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("start_date", sa.Date, nullable=True),
    )
    sa.Table(
        "compensation_change",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("employee_id", sa.Integer, sa.ForeignKey("employee.id"), nullable=False),
        sa.Column("position", sa.String(100), nullable=False),
        sa.Column("salary", sa.Numeric(12, 2), nullable=False),
        sa.Column("effective_date", sa.Date, nullable=False),
        sa.Column("reason", sa.String(100), nullable=False),
        sa.Column("recorded_at", sa.DateTime, nullable=False),
    )
    sa.Table(
        "time_off_request",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("employee_id", sa.Integer, sa.ForeignKey("employee.id"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("start_date", sa.Date, nullable=False),
        sa.Column("end_date", sa.Date, nullable=False),
        sa.Column("note", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("requested_at", sa.DateTime, nullable=False),
        sa.Column("decided_by_id", sa.Integer, sa.ForeignKey("employee.id"), nullable=True),
        sa.Column("decided_at", sa.DateTime, nullable=True),
    )
    return metadata
