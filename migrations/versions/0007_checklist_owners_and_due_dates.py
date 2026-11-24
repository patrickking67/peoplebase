"""An owner and a due date on each checklist task.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

# The owner each standard task gets in existing rows. Anything else stays with HR.
OWNERS = {
    "Payroll and tax details collected": "employee",
    "First-week plan agreed with their manager": "manager",
    "Last working day confirmed in writing": "manager",
    "Handover notes written": "employee",
    "Laptop and badge returned": "employee",
}


def upgrade():
    with op.batch_alter_table("checklist_task") as batch:
        batch.add_column(sa.Column("owner", sa.String(length=20), nullable=False, server_default="hr"))
        batch.add_column(sa.Column("due_date", sa.Date(), nullable=True))
    task = sa.table("checklist_task", sa.column("title", sa.String), sa.column("owner", sa.String))
    for title, owner in OWNERS.items():
        op.execute(task.update().where(task.c.title == title).values(owner=owner))


def downgrade():
    with op.batch_alter_table("checklist_task") as batch:
        batch.drop_column("due_date")
        batch.drop_column("owner")
