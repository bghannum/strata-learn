"""DB-backed session tokens (ADR-007: self-implemented, not a signed cookie —
see docs/adr/ADR-007-self-implemented-auth.md and the Phase 4b plan). The
cookie carries one random opaque token; only its SHA-256 hash is ever
persisted, so a DB compromise doesn't hand out usable sessions — the same
defense-in-depth reasoning as password hashing (security.py), just with a
fast hash instead of bcrypt since this is high-entropy random data, not a
low-entropy guessable secret.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlmodel import delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import Session, User

SESSION_LIFETIME = timedelta(days=30)


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


async def create_session(session: AsyncSession, user_id: UUID) -> str:
    """Returns the raw token — the only time it exists outside the cookie;
    only its hash gets persisted."""
    raw_token = secrets.token_urlsafe(32)
    db_session = Session(
        user_id=user_id,
        token_hash=_hash_token(raw_token),
        expires_at=datetime.now(UTC) + SESSION_LIFETIME,
    )
    session.add(db_session)
    await session.commit()
    return raw_token


async def get_user_from_token(session: AsyncSession, raw_token: str) -> User | None:
    db_session = (
        await session.exec(select(Session).where(Session.token_hash == _hash_token(raw_token)))
    ).first()
    if db_session is None:
        return None
    if db_session.expires_at < datetime.now(UTC):
        return None
    return await session.get(User, db_session.user_id)


async def delete_session(session: AsyncSession, raw_token: str) -> None:
    await session.exec(delete(Session).where(Session.token_hash == _hash_token(raw_token)))
    await session.commit()
