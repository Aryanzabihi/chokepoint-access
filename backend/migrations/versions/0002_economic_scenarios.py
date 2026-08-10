"""economic scenarios — saved runs of the engine.md decision engine

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-10
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "economicscenario",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("client.id"), nullable=True),
        sa.Column("exposure_id", sa.Integer(), sa.ForeignKey("exposure.id"), nullable=True),
        sa.Column("scenario_id", sa.String(), nullable=False),
        sa.Column("corridor", sa.String(), nullable=False),
        sa.Column("input_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_economicscenario_owner_user_id", "economicscenario", ["owner_user_id"])
    op.create_index("ix_economicscenario_client_id", "economicscenario", ["client_id"])
    op.create_index("ix_economicscenario_exposure_id", "economicscenario", ["exposure_id"])


def downgrade() -> None:
    op.drop_table("economicscenario")
