"""negotiation briefs replace simulated negotiation rounds

Adds ``negotiation_briefs`` (what to ask a provider for, plus the outcome the
user reports) and drops ``negotiation_history``, which only ever held rounds
played against the built-in provider simulator.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-04 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'negotiation_briefs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('subscription_id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('current_cost', sa.Float(), nullable=False),
        sa.Column('effective_cost', sa.Float(), nullable=False),
        sa.Column('opening_ask', sa.Float(), nullable=False),
        sa.Column('walk_away_price', sa.Float(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('draft_message', sa.Text(), nullable=False),
        sa.Column('included_usage', sa.Boolean(), nullable=False),
        sa.Column('agreed_price', sa.Float(), nullable=True),
        sa.Column('outcome_note', sa.Text(), nullable=True),
        sa.Column('reported_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('open', 'accepted', 'declined', 'dismissed')",
                           name=op.f('ck_negotiation_briefs_valid_status')),
        sa.ForeignKeyConstraint(['run_id'], ['agent_runs.id'],
                                name=op.f('fk_negotiation_briefs_run_id_agent_runs'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['subscription_id'], ['subscriptions.id'],
                                name=op.f('fk_negotiation_briefs_subscription_id_subscriptions'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'],
                                name=op.f('fk_negotiation_briefs_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_negotiation_briefs')),
    )
    with op.batch_alter_table('negotiation_briefs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_negotiation_briefs_run_id'), ['run_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_negotiation_briefs_subscription_id'), ['subscription_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_negotiation_briefs_user_id'), ['user_id'], unique=False)

    with op.batch_alter_table('negotiation_history', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_negotiation_history_subscription_id'))
        batch_op.drop_index(batch_op.f('ix_negotiation_history_id'))
    op.drop_table('negotiation_history')


def downgrade() -> None:
    """Downgrade schema (the simulated rounds are not restored)."""
    op.create_table(
        'negotiation_history',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('subscription_id', sa.Integer(), nullable=False),
        sa.Column('round_number', sa.Integer(), nullable=False),
        sa.Column('agent_offer', sa.Float(), nullable=False),
        sa.Column('provider_counter', sa.Float(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['subscription_id'], ['subscriptions.id'],
                                name=op.f('fk_negotiation_history_subscription_id_subscriptions'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_negotiation_history')),
    )
    with op.batch_alter_table('negotiation_history', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_negotiation_history_id'), ['id'], unique=False)
        batch_op.create_index(batch_op.f('ix_negotiation_history_subscription_id'), ['subscription_id'], unique=False)

    with op.batch_alter_table('negotiation_briefs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_negotiation_briefs_user_id'))
        batch_op.drop_index(batch_op.f('ix_negotiation_briefs_subscription_id'))
        batch_op.drop_index(batch_op.f('ix_negotiation_briefs_run_id'))
    op.drop_table('negotiation_briefs')
