"""add chunk_index and chunk_count to modulesummary

Revision ID: d4153e87b2db
Revises: 770b65387af4
Create Date: 2026-08-15 09:33:42.124374

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4153e87b2db'
down_revision: Union[str, Sequence[str], None] = '770b65387af4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Autogenerate emitted bare NOT NULL add_columns, which have no value to
    # backfill existing rows with — the same correction 770b65387af4 needed for
    # quiz.feedback_mode. server_default='1' backfills every existing row to
    # "part 1 of 1", which is the truthful reading for them: multi-chunk files
    # are rare enough that no snapshot indexed before this migration is likely
    # to have any, and for the single-chunk rows that make up the rest, 1 of 1
    # is exactly right. The default is then dropped so new inserts carry the
    # app-level default instead of a DB-level one, matching how every other
    # defaulted column in this schema is handled.
    op.add_column('modulesummary', sa.Column('chunk_index', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('modulesummary', sa.Column('chunk_count', sa.Integer(), nullable=False, server_default='1'))
    op.alter_column('modulesummary', 'chunk_index', server_default=None)
    op.alter_column('modulesummary', 'chunk_count', server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('modulesummary', 'chunk_count')
    op.drop_column('modulesummary', 'chunk_index')
