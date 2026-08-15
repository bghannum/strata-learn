"""add subsystem key to question

Revision ID: 66328312cb10
Revises: a313df56e2db
Create Date: 2026-08-15 10:43:13.585438

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '66328312cb10'
down_revision: Union[str, Sequence[str], None] = 'a313df56e2db'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Autogenerate's output is correct as-is here, unlike 770b65387af4 and
    # d4153e87b2db: the column is nullable, so existing rows need no backfill
    # and no server_default-then-drop dance. Null is also the truthful value for
    # them — questions generated before subsystems existed genuinely have no
    # subsystem, and mastery aggregation buckets those as "ungrouped" rather
    # than inventing membership for them.
    op.add_column('question', sa.Column('subsystem_key', sqlmodel.sql.sqltypes.AutoString(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('question', 'subsystem_key')
