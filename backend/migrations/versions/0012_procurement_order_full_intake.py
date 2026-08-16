"""Every intake.FIELDS field not already on ProcurementOrder (demand_supply/
disruption_assumptions/economic_exposure groups), plus the three top-level
probability fields -- collected once on the order instead of re-asked on
every decision built from it. All nullable, same as every other optional
order field; no server_default needed.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-16
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

_NEW_COLUMNS = (
    "days_of_cover", "delay_days_estimate", "forecast_quantity", "forecast_window_days",
    "current_inventory", "inbound_confirmed_quantity", "safety_stock", "wacc_pct",
    "carrying_cost_pct_pa", "gross_margin_pct", "penalty_per_day", "disrupted_freight_quote",
    "reroute_quote", "war_risk_premium_quote", "emergency_replacement_quote",
    "client_probability_estimate", "probability_range_low", "probability_range_high",
)


def upgrade() -> None:
    with op.batch_alter_table("procurementorder") as batch_op:
        for name in _NEW_COLUMNS:
            batch_op.add_column(sa.Column(name, sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("procurementorder") as batch_op:
        for name in reversed(_NEW_COLUMNS):
            batch_op.drop_column(name)
