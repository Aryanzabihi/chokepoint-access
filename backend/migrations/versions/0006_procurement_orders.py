"""procurement orders — the pre-order / PO-placed / in-transit / delivered lifecycle

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-14
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "procurementorder",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("client.id"), nullable=True),
        sa.Column("exposure_id", sa.Integer(), sa.ForeignKey("exposure.id"), nullable=True),
        sa.Column("corridor", sa.String(), nullable=False),
        sa.Column("stage", sa.String(), nullable=False, server_default="pre_order"),
        sa.Column("sku", sa.String(), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("quantity_unit", sa.String(), nullable=False),
        sa.Column("cargo_value", sa.Float(), nullable=True),
        sa.Column("incoterm", sa.String(), nullable=True),
        sa.Column("supplier", sa.String(), nullable=True),
        sa.Column("currency", sa.String(), nullable=False, server_default="EUR"),
        sa.Column("po_number", sa.String(), nullable=True),
        sa.Column("unit_price", sa.Float(), nullable=True),
        sa.Column("ship_date", sa.String(), nullable=True),
        sa.Column("contract_transit_time_days", sa.Float(), nullable=True),
        sa.Column("contract_freight_rate", sa.Float(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_procurementorder_owner_user_id", "procurementorder", ["owner_user_id"])
    op.create_index("ix_procurementorder_client_id", "procurementorder", ["client_id"])
    op.create_index("ix_procurementorder_exposure_id", "procurementorder", ["exposure_id"])


def downgrade() -> None:
    op.drop_table("procurementorder")
