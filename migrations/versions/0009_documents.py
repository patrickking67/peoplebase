"""Employee documents.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "document",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=100), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=False),
        sa.Column("audience", sa.String(length=20), nullable=False),
        sa.Column("original_name", sa.String(length=150), nullable=False),
        sa.Column("content_type", sa.String(length=40), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("stored_name", sa.String(length=40), nullable=False),
        sa.Column("uploaded_by", sa.String(length=254), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["employee_id"], ["employee.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stored_name"),
    )
    op.create_index(op.f("ix_document_employee_id"), "document", ["employee_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_document_employee_id"), table_name="document")
    op.drop_table("document")
