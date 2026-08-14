"""procurement orders — origin, destination, supplier lead time, alternative
supplier (the fields the "New Decision" wizard's Procurement/Order step
needs that weren't on the table yet). All nullable, no server_default
needed since none are NOT NULL.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-14
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("procurementorder") as batch_op:
        batch_op.add_column(sa.Column("origin", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("destination", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("supplier_lead_time_days", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("alternative_supplier", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("procurementorder") as batch_op:
        batch_op.drop_column("origin")
        batch_op.drop_column("destination")
        batch_op.drop_column("supplier_lead_time_days")
        batch_op.drop_column("alternative_supplier")
