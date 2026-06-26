"""add db uuid defaults for auth tables

Revision ID: d4a1c2b3e5f6
Revises: c8f0a1d2e3b4
Create Date: 2026-06-26 00:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4a1c2b3e5f6"
down_revision: Union[str, Sequence[str], None] = "c8f0a1d2e3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.alter_column(
        "users",
        "id",
        existing_type=sa.UUID(),
        server_default=sa.text("gen_random_uuid()"),
    )
    op.alter_column(
        "user_sessions",
        "id",
        existing_type=sa.UUID(),
        server_default=sa.text("gen_random_uuid()"),
    )


def downgrade() -> None:
    op.alter_column("user_sessions", "id", existing_type=sa.UUID(), server_default=None)
    op.alter_column("users", "id", existing_type=sa.UUID(), server_default=None)
