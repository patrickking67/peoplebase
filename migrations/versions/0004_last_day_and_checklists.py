"""A last working day on the employee, and onboarding and offboarding tasks.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-11
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    # Batch mode rebuilds the table on SQLite, which cannot drop columns or add
    # constraints in place, and is a plain ALTER on PostgreSQL.
    with op.batch_alter_table("employee") as batch:
        batch.add_column(sa.Column("end_date", sa.Date(), nullable=True))
    op.create_table(
        "checklist_task",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("completed_by", sa.String(length=254), nullable=True),
        sa.ForeignKeyConstraint(["employee_id"], ["employee.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_checklist_task_employee_id"), "checklist_task", ["employee_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_checklist_task_employee_id"), table_name="checklist_task")
    op.drop_table("checklist_task")
    with op.batch_alter_table("employee") as batch:
        batch.drop_column("end_date")
