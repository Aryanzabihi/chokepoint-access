"""economic scenario subscriptions — monthly re-evaluation opt-in

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-11
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "economicscenariosubscription",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("economic_scenario_id", sa.Integer(),
                  sa.ForeignKey("economicscenario.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_economicscenariosubscription_user_id",
                     "economicscenariosubscription", ["user_id"])
    op.create_index("ix_economicscenariosubscription_economic_scenario_id",
                     "economicscenariosubscription", ["economic_scenario_id"])


def downgrade() -> None:
    op.drop_table("economicscenariosubscription")
