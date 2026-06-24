"""add invoke_tasks

Revision ID: a3c5e7f90123
Revises: f2b4fcbf8369
Create Date: 2026-06-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON, UUID


revision: str = 'a3c5e7f90123'
down_revision: Union[str, Sequence[str], None] = 'f2b4fcbf8369'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('invoke_tasks',
        sa.Column('id', UUID(as_uuid=True), nullable=False),
        sa.Column('agent_id', UUID(as_uuid=True), nullable=False),
        sa.Column('agent_name', sa.String(length=64), nullable=False),
        sa.Column('request_id', sa.String(length=64), nullable=False),
        sa.Column('webhook_url', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='pending'),
        sa.Column('input_payload', JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column('output_payload', JSON(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('trace_id', sa.String(length=64), nullable=True),
        sa.Column('webhook_delivered', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_invoke_tasks_request_id'), 'invoke_tasks', ['request_id'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_invoke_tasks_request_id'), table_name='invoke_tasks')
    op.drop_table('invoke_tasks')
