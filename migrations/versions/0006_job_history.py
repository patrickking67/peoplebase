"""An explicit job history, filled from the existing compensation history.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    job_change = op.create_table(
        "job_change",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.String(length=100), nullable=False),
        sa.Column("department_name", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("manager_name", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["employee_id"], ["employee.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_job_change_employee_id"), "job_change", ["employee_id"], unique=False)

    bind = op.get_bind()
    compensation = sa.table(
        "compensation_change",
        sa.column("id", sa.Integer),
        sa.column("employee_id", sa.Integer),
        sa.column("position", sa.String),
        sa.column("effective_date", sa.Date),
        sa.column("reason", sa.String),
        sa.column("recorded_at", sa.DateTime),
    )
    employee = sa.table(
        "employee",
        sa.column("id", sa.Integer),
        sa.column("department_id", sa.Integer),
        sa.column("manager_id", sa.Integer),
        sa.column("name", sa.String),
    )
    department = sa.table("department", sa.column("id", sa.Integer), sa.column("name", sa.String))

    rows = bind.execute(
        sa.select(compensation).order_by(compensation.c.employee_id, compensation.c.effective_date, compensation.c.id)
    ).all()
    current = {}
    for person in bind.execute(sa.select(employee)).all():
        dept = bind.execute(sa.select(department.c.name).where(department.c.id == person.department_id)).scalar()
        boss = bind.execute(sa.select(employee.c.name).where(employee.c.id == person.manager_id)).scalar()
        current[person.id] = (dept or "", boss or "")

    # Only a different title is a job change; a pay-only change stays in compensation_change.
    kept = []
    previous = {}
    for row in rows:
        if previous.get(row.employee_id) != row.position:
            kept.append(row)
        previous[row.employee_id] = row.position
    last_for = {row.employee_id: row.id for row in kept}
    inserts = []
    for row in kept:
        # The newest title is the one the person holds now, so it can carry
        # their current department and manager. Older rows predate that record.
        is_newest = last_for[row.employee_id] == row.id
        department_name, manager_name = current.get(row.employee_id, ("", "")) if is_newest else ("", "")
        inserts.append(
            {
                "employee_id": row.employee_id,
                "position": row.position,
                "department_name": department_name,
                "manager_name": manager_name,
                "effective_date": row.effective_date,
                "reason": row.reason or "",
                "recorded_at": row.recorded_at,
            }
        )
    op.bulk_insert(job_change, inserts)


def downgrade():
    op.drop_index(op.f("ix_job_change_employee_id"), table_name="job_change")
    op.drop_table("job_change")
