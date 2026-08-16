"""add short_answer questions: enum value, rubric, rubric_hits

Revision ID: e7a2c9d1f3b4
Revises: b6c011e4a3d2
Create Date: 2026-08-16 15:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7a2c9d1f3b4'
down_revision: Union[str, Sequence[str], None] = 'b6c011e4a3d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # questiontype is a native Postgres enum (see the Phase 5 migration that
    # created it). ADD VALUE is the supported way to widen one; it can't be
    # used inside the same transaction that adds it, which is fine here —
    # nothing below inserts a row with the new value.
    op.execute("ALTER TYPE questiontype ADD VALUE IF NOT EXISTS 'short_answer'")
    # Both nullable — every existing question and submission is an mcq or
    # fill_blank, for which null is the truthful value.
    op.add_column('question', sa.Column('rubric', sa.JSON(), nullable=True))
    op.add_column('answersubmission', sa.Column('rubric_hits', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('answersubmission', 'rubric_hits')
    op.drop_column('question', 'rubric')
    # Postgres has no DROP VALUE for enums; the value is harmless to leave.
