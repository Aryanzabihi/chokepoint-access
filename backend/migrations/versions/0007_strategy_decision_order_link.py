"""link strategy decisions to procurement orders, add an approval status

First op.add_column migration in this repo (every prior migration only ever
created a table) -- server_default is required on `status` since it's
NOT NULL and existing rows need a value. Both directions go through
batch_alter_table: confirmed by actually running this against SQLite (not
just reasoned about) that plain op.add_column with a foreign-key constraint
raises NotImplementedError on SQLite's dialect ("No support for ALTER of
constraints... refer to batch mode") -- this bites upgrade(), not just the
drop_column-heavy downgrade() a naive read of SQLite's ALTER TABLE
limitations would suggest. Postgres supports the plain form fine; batch
mode degrades to it automatically there, so this is strictly safer, not
just a SQLite workaround.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-14
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("strategydecision") as batch_op:
        batch_op.add_column(sa.Column("order_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("status", sa.String(), nullable=False,
                                       server_default="draft"))
        batch_op.create_foreign_key("fk_strategydecision_order_id_procurementorder",
                                     "procurementorder", ["order_id"], ["id"])
    op.create_index("ix_strategydecision_order_id", "strategydecision", ["order_id"])


def downgrade() -> None:
    op.drop_index("ix_strategydecision_order_id", table_name="strategydecision")
    with op.batch_alter_table("strategydecision") as batch_op:
        batch_op.drop_constraint("fk_strategydecision_order_id_procurementorder",
                                  type_="foreignkey")
        batch_op.drop_column("order_id")
        batch_op.drop_column("status")
