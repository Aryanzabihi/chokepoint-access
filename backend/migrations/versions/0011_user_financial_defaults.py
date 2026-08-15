"""user financial-assumption defaults -- optional, edited on /settings.
Nullable, same as company_name, no server_default needed.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-15
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("user") as batch_op:
        batch_op.add_column(sa.Column("default_wacc_pct", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("default_carrying_cost_pct_pa", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("default_gross_margin_pct", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("default_penalty_per_day", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_column("default_penalty_per_day")
        batch_op.drop_column("default_gross_margin_pct")
        batch_op.drop_column("default_carrying_cost_pct_pa")
        batch_op.drop_column("default_wacc_pct")
