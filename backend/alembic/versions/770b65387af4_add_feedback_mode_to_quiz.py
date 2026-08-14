"""add feedback_mode to quiz

Revision ID: 770b65387af4
Revises: 0fc78d165a2b
Create Date: 2026-08-14 13:33:03.209007

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '770b65387af4'
down_revision: Union[str, Sequence[str], None] = '0fc78d165a2b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


feedback_mode_enum = sa.Enum('immediate', 'end_of_quiz', name='feedbackmode')


def upgrade() -> None:
    """Upgrade schema."""
    # Autogenerate emitted a bare add_column, which fails two ways against a
    # non-empty, already-existing table: the `feedbackmode` Postgres enum
    # type doesn't exist yet (every other enum column in this schema was
    # added as part of a CREATE TABLE, where SQLAlchemy creates the type
    # implicitly — a standalone ADD COLUMN on an existing table needs it
    # created explicitly first), and a NOT NULL column with no default has
    # no value to backfill existing rows with. server_default backfills to
    # the same end_of_quiz default the Python model uses, then gets dropped
    # so new inserts rely on the app-level default instead of a DB-level
    # one, matching how the table's other enum columns (status) are handled.
    feedback_mode_enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        'quiz',
        sa.Column('feedback_mode', feedback_mode_enum, nullable=False, server_default='end_of_quiz'),
    )
    op.alter_column('quiz', 'feedback_mode', server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('quiz', 'feedback_mode')
    feedback_mode_enum.drop(op.get_bind(), checkfirst=True)
