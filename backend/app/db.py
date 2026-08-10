"""db.py — engine/session setup.

DATABASE_URL comes from the environment (Render injects it automatically for
a linked Postgres; locally it's set via .env / docker-compose). No default
that silently points at a real database — a missing DATABASE_URL should
fail loudly, not fall back to something surprising.
"""

from __future__ import annotations

import os
from collections.abc import Generator

from sqlmodel import Session, SQLModel, create_engine

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. Locally: copy backend/.env.example to "
        "backend/.env and point it at your local Postgres (see "
        "backend/docker-compose.yml). On Render: linking the Postgres "
        "instance in render.yaml sets this automatically."
    )

# Render's Postgres URLs use the postgres:// scheme; SQLAlchemy wants
# postgresql://. Normalise rather than making every caller remember this.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def init_db() -> None:
    """Dev convenience only — production schema changes go through Alembic."""
    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
