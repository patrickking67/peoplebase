"""Baseline: the schema as it stood before versioned migrations.

Revision ID: 0001
Revises:
Create Date: 2026-11-11

Earlier versions created their tables with db.create_all() and patched the
employee table in place, so real databases exist in three shapes: empty, the
first release (an employee table with four columns) and the four-table schema.
None of them has an alembic_version table. This revision therefore creates
only what is missing, and every shape ends up identical.
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Columns the first release did not have. Each is nullable or has a default,
# so existing rows stay valid.
LATER_EMPLOYEE_COLUMNS = {
    "department_id": "INTEGER REFERENCES department (id)",
    "manager_id": "INTEGER REFERENCES employee (id)",
    "status": "VARCHAR(20) NOT NULL DEFAULT 'active'",
    "start_date": "DATE",
}


def upgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())

    if "department" not in tables:
        op.create_table(
            "department",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("name"),
        )

    if "employee" not in tables:
        op.create_table(
            "employee",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.Column("position", sa.String(length=100), nullable=False),
            sa.Column("salary", sa.Numeric(precision=12, scale=2), nullable=False),
            sa.Column("department_id", sa.Integer(), nullable=True),
            sa.Column("manager_id", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
            sa.Column("start_date", sa.Date(), nullable=True),
            sa.ForeignKeyConstraint(["department_id"], ["department.id"]),
            sa.ForeignKeyConstraint(["manager_id"], ["employee.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    else:
        existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("employee")}
        for name, definition in LATER_EMPLOYEE_COLUMNS.items():
            if name not in existing:
                op.execute(f"ALTER TABLE employee ADD COLUMN {name} {definition}")

    if "compensation_change" not in tables:
        op.create_table(
            "compensation_change",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("employee_id", sa.Integer(), nullable=False),
            sa.Column("position", sa.String(length=100), nullable=False),
            sa.Column("salary", sa.Numeric(precision=12, scale=2), nullable=False),
            sa.Column("effective_date", sa.Date(), nullable=False),
            sa.Column("reason", sa.String(length=100), nullable=False),
            sa.Column("recorded_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["employee_id"], ["employee.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    if "time_off_request" not in tables:
        op.create_table(
            "time_off_request",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("employee_id", sa.Integer(), nullable=False),
            sa.Column("kind", sa.String(length=20), nullable=False),
            sa.Column("start_date", sa.Date(), nullable=False),
            sa.Column("end_date", sa.Date(), nullable=False),
            sa.Column("note", sa.String(length=200), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("requested_at", sa.DateTime(), nullable=False),
            sa.Column("decided_by_id", sa.Integer(), nullable=True),
            sa.Column("decided_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["decided_by_id"], ["employee.id"]),
            sa.ForeignKeyConstraint(["employee_id"], ["employee.id"]),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade():
    op.drop_table("time_off_request")
    op.drop_table("compensation_change")
    op.drop_table("employee")
    op.drop_table("department")
