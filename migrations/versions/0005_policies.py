"""Policies and their acknowledgements.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-11
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "policy",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=False),
        sa.Column("published_by", sa.String(length=254), nullable=False),
        sa.Column("retired_at", sa.DateTime(), nullable=True),
        sa.Column("replaces_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["replaces_id"], ["policy.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "acknowledgement",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("policy_id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["employee_id"], ["employee.id"]),
        sa.ForeignKeyConstraint(["policy_id"], ["policy.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("policy_id", "employee_id", name="uq_acknowledgement_policy_employee"),
    )
    op.create_index(op.f("ix_acknowledgement_employee_id"), "acknowledgement", ["employee_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_acknowledgement_employee_id"), table_name="acknowledgement")
    op.drop_table("acknowledgement")
    op.drop_table("policy")
