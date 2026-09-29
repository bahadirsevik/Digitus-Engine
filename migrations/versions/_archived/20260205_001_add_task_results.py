"""Add task_results table

Revision ID: 20260205_001
Revises: 
Create Date: 2026-02-05
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = '20260205_001_add_task_results'
down_revision = None  # Update this if there's a previous migration
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'task_results',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('task_id', sa.String(50), nullable=False),
        sa.Column('task_type', sa.String(50), nullable=True),
        sa.Column('scoring_run_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(20), nullable=True, default='pending'),
        sa.Column('progress', sa.Integer(), nullable=True, default=0),
        sa.Column('result_data', sa.JSON(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['scoring_run_id'], ['scoring_runs.id'], ondelete='SET NULL'),
    )
    
    # Indexes
    op.create_index(op.f('ix_task_results_task_id'), 'task_results', ['task_id'], unique=True)
    op.create_index(op.f('ix_task_results_task_type'), 'task_results', ['task_type'])
    op.create_index(op.f('ix_task_results_status'), 'task_results', ['status'])


def downgrade() -> None:
    op.drop_index(op.f('ix_task_results_status'), table_name='task_results')
    op.drop_index(op.f('ix_task_results_task_type'), table_name='task_results')
    op.drop_index(op.f('ix_task_results_task_id'), table_name='task_results')
    op.drop_table('task_results')
