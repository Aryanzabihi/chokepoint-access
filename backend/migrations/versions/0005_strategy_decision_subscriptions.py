"""strategy decision subscriptions — monthly re-evaluation opt-in

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-13
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "strategydecisionsubscription",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("strategy_decision_id", sa.Integer(),
                  sa.ForeignKey("strategydecision.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_strategydecisionsubscription_user_id",
                     "strategydecisionsubscription", ["user_id"])
    op.create_index("ix_strategydecisionsubscription_strategy_decision_id",
                     "strategydecisionsubscription", ["strategy_decision_id"])


def downgrade() -> None:
    op.drop_table("strategydecisionsubscription")
