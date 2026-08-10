"""initial schema — users, clients, exposures, decisions, audit, api keys, alerts

Revision ID: 0001
Revises:
Create Date: 2026-08-10
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_user_email", "user", ["email"], unique=True)

    op.create_table(
        "client",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_client_owner_user_id", "client", ["owner_user_id"])

    op.create_table(
        "exposure",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("client.id"), nullable=False),
        sa.Column("corridor", sa.String(), nullable=False),
        sa.Column("commodity", sa.String(), nullable=True),
        sa.Column("annual_exposure", sa.Float(), nullable=True),
        sa.Column("crisis_replacement_cost", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False, server_default="EUR"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_exposure_client_id", "exposure", ["client_id"])

    op.create_table(
        "decision",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("exposure_id", sa.Integer(), sa.ForeignKey("exposure.id"), nullable=False),
        sa.Column("as_of", sa.String(), nullable=False),
        sa.Column("window_months", sa.Integer(), nullable=False),
        sa.Column("alpha", sa.Float(), nullable=False),
        sa.Column("tar", sa.Float(), nullable=False),
        sa.Column("band", sa.String(), nullable=False),
        sa.Column("regime", sa.String(), nullable=False),
        sa.Column("decision_level", sa.String(), nullable=False),
        sa.Column("decision_margin_pp", sa.Float(), nullable=True),
        sa.Column("computed_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_decision_exposure_id", "decision", ["exposure_id"])

    op.create_table(
        "auditevent",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("entity_type", sa.String(), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("detail", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_auditevent_user_id", "auditevent", ["user_id"])

    op.create_table(
        "apikey",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("hashed_key", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_apikey_user_id", "apikey", ["user_id"])
    op.create_index("ix_apikey_hashed_key", "apikey", ["hashed_key"], unique=True)

    op.create_table(
        "alertsubscription",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("exposure_id", sa.Integer(), sa.ForeignKey("exposure.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_alertsubscription_user_id", "alertsubscription", ["user_id"])
    op.create_index("ix_alertsubscription_exposure_id", "alertsubscription", ["exposure_id"])


def downgrade() -> None:
    op.drop_table("alertsubscription")
    op.drop_table("apikey")
    op.drop_table("auditevent")
    op.drop_table("decision")
    op.drop_table("exposure")
    op.drop_table("client")
    op.drop_table("user")
