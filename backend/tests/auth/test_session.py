from datetime import UTC, datetime, timedelta

from sqlmodel import select

from app.auth.security import hash_password
from app.auth.session import _hash_token, create_session, delete_session, get_user_from_token
from app.db.models import Session, User
from app.db.session import async_session_factory


async def _make_user(email: str = "session-test@example.com") -> User:
    async with async_session_factory() as session:
        user = User(email=email, password_hash=hash_password("irrelevant-for-these-tests"))
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def test_create_session_token_resolves_to_the_right_user() -> None:
    user = await _make_user()
    async with async_session_factory() as session:
        token = await create_session(session, user.id)

    async with async_session_factory() as session:
        resolved = await get_user_from_token(session, token)

    assert resolved is not None
    assert resolved.id == user.id


async def test_get_user_from_token_returns_none_for_unknown_token() -> None:
    async with async_session_factory() as session:
        assert await get_user_from_token(session, "not-a-real-token") is None


async def test_get_user_from_token_returns_none_for_expired_session() -> None:
    user = await _make_user()
    async with async_session_factory() as session:
        token = await create_session(session, user.id)

    # Backdate the session's expiry directly — create_session always mints a
    # fresh 30-day expiry, so this is the only way to exercise the expired
    # path without waiting 30 days.
    async with async_session_factory() as session:
        db_session = (await session.exec(select(Session).where(Session.token_hash == _hash_token(token)))).first()
        assert db_session is not None
        db_session.expires_at = datetime.now(UTC) - timedelta(days=1)
        session.add(db_session)
        await session.commit()

    async with async_session_factory() as session:
        assert await get_user_from_token(session, token) is None


async def test_delete_session_invalidates_the_token() -> None:
    user = await _make_user()
    async with async_session_factory() as session:
        token = await create_session(session, user.id)

    async with async_session_factory() as session:
        await delete_session(session, token)

    async with async_session_factory() as session:
        assert await get_user_from_token(session, token) is None


async def test_delete_session_tolerates_an_already_gone_token() -> None:
    # A double logout (e.g. two tabs) must not raise.
    async with async_session_factory() as session:
        await delete_session(session, "never-existed")
