"""add model providers and llm models

Revision ID: b5d8e2a91034
Revises: a3c5e7f90123
Create Date: 2026-06-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b5d8e2a91034"
down_revision: Union[str, Sequence[str], None] = "a3c5e7f90123"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "model_providers",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_model_providers_name"), "model_providers", ["name"], unique=True)

    op.create_table(
        "llm_models",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("provider_id", sa.UUID(), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("max_tokens", sa.Integer(), nullable=False),
        sa.Column("temperature_default", sa.Float(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["provider_id"], ["model_providers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_llm_models_is_default"), "llm_models", ["is_default"], unique=False)
    op.create_index(op.f("ix_llm_models_provider_id"), "llm_models", ["provider_id"], unique=False)

    op.add_column("agents", sa.Column("model_id", sa.UUID(), nullable=True))
    op.create_index(op.f("ix_agents_model_id"), "agents", ["model_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_agents_model_id_llm_models"),
        "agents",
        "llm_models",
        ["model_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f("fk_agents_model_id_llm_models"), "agents", type_="foreignkey")
    op.drop_index(op.f("ix_agents_model_id"), table_name="agents")
    op.drop_column("agents", "model_id")
    op.drop_index(op.f("ix_llm_models_provider_id"), table_name="llm_models")
    op.drop_index(op.f("ix_llm_models_is_default"), table_name="llm_models")
    op.drop_table("llm_models")
    op.drop_index(op.f("ix_model_providers_name"), table_name="model_providers")
    op.drop_table("model_providers")
