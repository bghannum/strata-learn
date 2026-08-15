"""add update check fields to repo

Revision ID: b6c011e4a3d2
Revises: 66328312cb10
Create Date: 2026-08-15 10:59:10.953697

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'b6c011e4a3d2'
down_revision: Union[str, Sequence[str], None] = '66328312cb10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Both nullable — an existing repo has genuinely never been checked, and
    # null is the truthful value for that rather than a backfilled placeholder.
    op.add_column('repo', sa.Column('remote_head_commit', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.add_column('repo', sa.Column('updates_checked_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('repo', 'updates_checked_at')
    op.drop_column('repo', 'remote_head_commit')
