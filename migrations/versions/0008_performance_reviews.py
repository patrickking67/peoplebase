"""Performance review cycles and reviews.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "review_cycle",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("self_due", sa.Date(), nullable=False),
        sa.Column("manager_due", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(length=254), nullable=False),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "review",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("cycle_id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("self_rating", sa.Integer(), nullable=True),
        sa.Column("self_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("self_submitted_at", sa.DateTime(), nullable=True),
        sa.Column("manager_rating", sa.Integer(), nullable=True),
        sa.Column("manager_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("manager_submitted_at", sa.DateTime(), nullable=True),
        sa.Column("manager_submitted_by", sa.String(length=254), nullable=True),
        sa.ForeignKeyConstraint(["cycle_id"], ["review_cycle.id"]),
        sa.ForeignKeyConstraint(["employee_id"], ["employee.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cycle_id", "employee_id", name="uq_review_cycle_employee"),
    )
    op.create_index(op.f("ix_review_cycle_id"), "review", ["cycle_id"], unique=False)
    op.create_index(op.f("ix_review_employee_id"), "review", ["employee_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_review_employee_id"), table_name="review")
    op.drop_index(op.f("ix_review_cycle_id"), table_name="review")
    op.drop_table("review")
    op.drop_table("review_cycle")
