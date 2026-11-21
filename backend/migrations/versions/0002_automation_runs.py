"""automation runs (lead automation stages A-E)

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-21 18:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('automation_runs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('investigation_id', sa.String(length=36), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('config', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('progress', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('counters', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('log', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('stop_reason', sa.Text(), nullable=True),
    sa.Column('abort_requested', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.String(length=100), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['investigation_id'], ['investigations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('automation_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_automation_runs_investigation_id'), ['investigation_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_automation_runs_status'), ['status'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('automation_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_automation_runs_status'))
        batch_op.drop_index(batch_op.f('ix_automation_runs_investigation_id'))

    op.drop_table('automation_runs')
