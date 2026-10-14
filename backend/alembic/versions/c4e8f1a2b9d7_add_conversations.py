"""add conversations / conversation_turns

Revision ID: c4e8f1a2b9d7
Revises: a7c1e9f4b2d3
Create Date: 2026-10-11 09:15:00.000000

平台多轮会话（需求文档 §6）：会话表 + 轮次表。
- conversations 归属到 agent 与 API Key，api_key_id 用于调用方隔离；
- conversation_turns 的 (conversation_id, turn_index) 唯一约束防并发撞号，
  轮次随会话级联删除。

方言说明：需求文档给的 SQL 是 Postgres 风味，本仓库实际是 MySQL，
因此 JSONB → sa.JSON()、TIMESTAMPTZ → sa.DateTime(timezone=True)、UUID → sa.Uuid()。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4e8f1a2b9d7'
down_revision: Union[str, Sequence[str], None] = 'a7c1e9f4b2d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'conversations',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('agent_id', sa.Uuid(), nullable=False),
        sa.Column('api_key_id', sa.Uuid(), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['api_key_id'], ['api_keys.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'idx_conv_agent_created',
        'conversations',
        ['agent_id', 'created_at'],
        unique=False,
    )

    op.create_table(
        'conversation_turns',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('conversation_id', sa.Uuid(), nullable=False),
        sa.Column('turn_index', sa.Integer(), nullable=False),
        sa.Column('input', sa.JSON(), nullable=False),
        sa.Column('output', sa.JSON(), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('trace_id', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['conversation_id'], ['conversations.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('conversation_id', 'turn_index', name='uq_conv_turn'),
    )
    op.create_index(
        'idx_turn_conv',
        'conversation_turns',
        ['conversation_id', 'turn_index'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_turn_conv', table_name='conversation_turns')
    op.drop_table('conversation_turns')
    op.drop_index('idx_conv_agent_created', table_name='conversations')
    op.drop_table('conversations')
