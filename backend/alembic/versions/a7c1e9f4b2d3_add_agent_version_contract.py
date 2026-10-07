"""add agent_version contract

Revision ID: a7c1e9f4b2d3
Revises: bfc0ddcb9bcb
Create Date: 2026-10-07 22:30:00.000000

Agent 契约声明（agent.yaml / manifest.json）的解析结果，随版本存储。
列为可空：存量版本与未声明契约的新版本都是 NULL，无需数据回填。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c1e9f4b2d3'
down_revision: Union[str, Sequence[str], None] = 'bfc0ddcb9bcb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'agent_versions',
        sa.Column('contract', sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('agent_versions', 'contract')
