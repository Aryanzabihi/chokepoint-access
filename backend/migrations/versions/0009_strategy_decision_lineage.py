"""strategy decisions — previous_decision_id, a self-referential link so a
recalculated decision (new quotes plugged into the same order, same
strategies) points back at what it recalculated from -- the "decision
record" provenance workflow.md's reassess-with-TAR loop needs. Distinct
from order_id: two decisions can share an order without one being a
recalculation of the other (e.g. two independent "Modify" attempts).

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-14
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("strategydecision") as batch_op:
        batch_op.add_column(sa.Column("previous_decision_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_strategydecision_previous_decision_id_strategydecision",
                                     "strategydecision", ["previous_decision_id"], ["id"])
    op.create_index("ix_strategydecision_previous_decision_id", "strategydecision",
                    ["previous_decision_id"])


def downgrade() -> None:
    op.drop_index("ix_strategydecision_previous_decision_id", table_name="strategydecision")
    with op.batch_alter_table("strategydecision") as batch_op:
        batch_op.drop_constraint("fk_strategydecision_previous_decision_id_strategydecision",
                                  type_="foreignkey")
        batch_op.drop_column("previous_decision_id")
