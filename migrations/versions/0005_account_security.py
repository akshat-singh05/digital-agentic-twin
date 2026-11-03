"""account security: email verification, password reset, token revocation

Adds ``users.email_verified_at`` (existing accounts stay unverified until they
verify or reset their password), ``users.token_version`` (raising it signs the
user out everywhere) and ``account_tokens`` (single-use reset / verification
links, stored as hashes).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-04 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0005'
down_revision: Union[str, Sequence[str], None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('email_verified_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('token_version', sa.Integer(), server_default='0', nullable=False))

    op.create_table(
        'account_tokens',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('purpose', sa.String(length=10), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('used_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint("purpose IN ('reset', 'verify')",
                           name=op.f('ck_account_tokens_valid_purpose')),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'],
                                name=op.f('fk_account_tokens_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_account_tokens')),
        sa.UniqueConstraint('token_hash', name=op.f('uq_account_tokens_token_hash')),
    )
    with op.batch_alter_table('account_tokens', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_account_tokens_user_id'), ['user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('account_tokens', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_account_tokens_user_id'))
    op.drop_table('account_tokens')

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('token_version')
        batch_op.drop_column('email_verified_at')
