"""POST /auth/register, /auth/login, /auth/logout, GET /auth/me — session
issuance/validation for the self-implemented, DB-backed auth design (ADR-007,
app/auth/session.py). Every issuing endpoint sets the session cookie itself
directly on the Response rather than returning a token in the body — an
HttpOnly cookie can't be read or exfiltrated by JS, which is the whole point.

get_current_user is the dependency other routers (api/repos.py,
api/study_guides.py) import to scope requests to the logged-in user.
"""

import secrets
from asyncio import to_thread
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, EmailStr
from sqlmodel import select, update
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.security import hash_password, verify_password
from app.auth.session import (
    SESSION_LIFETIME,
    create_session,
    delete_session,
    get_user_from_token,
)
from app.config import settings
from app.db.models import Repo, User
from app.db.session import get_session

router = APIRouter(prefix="/auth", tags=["auth"])

SESSION_COOKIE_NAME = "session_token"

# checkpw() against a real bcrypt hash always fails (this isn't a real
# account's password), but still pays bcrypt's real verification cost — used
# below to equalize login timing between "no such user" and "wrong password"
# (found via Codex's Phase 4b pre-push review: user is None short-circuited
# past verify_password entirely, so response latency alone distinguished a
# registered email from an unregistered one even though the response body
# was already identical for both).
_DUMMY_PASSWORD_HASH = hash_password("not-a-real-account-timing-safety-only")


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    registration_secret: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    created_at: datetime


def _set_session_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_token,
        httponly=True,
        samesite="lax",
        # Plain http://localhost for now — a Secure cookie is silently
        # dropped over http, so this only becomes wrong once served over a
        # real HTTPS domain (already flagged as a Phase 7 deployment
        # checklist item in the original project plan).
        secure=False,
        max_age=int(SESSION_LIFETIME.total_seconds()),
    )


@router.post("/register", response_model=UserOut, status_code=201)
async def register(
    body: RegisterRequest, response: Response, session: AsyncSession = Depends(get_session)
) -> User:
    # ADR-007: this is a single-tenant app by design, not a general-purpose
    # signup surface — an unrestricted register endpoint would let anyone who
    # can reach the API create accounts that each enqueue real, paid
    # indexing/LLM work, which is a real exposure once this is ever deployed
    # beyond localhost (the original plan's own Phase 7 goal). The first
    # account ever created is the only one allowed. Checked unconditionally,
    # before even looking at the submitted email, and always returns the
    # same response either way — an unauthenticated caller getting a
    # different response for "this matches the registered email" versus
    # "this doesn't" is an email-enumeration oracle despite login's already
    # being careful about exactly that (found via Codex's Phase 4b pre-push
    # review, round 2).
    #
    # That lockout only closes the door *after* the first account exists —
    # on a freshly reachable deployment, whoever reaches this endpoint first
    # isn't necessarily the operator. registration_secret (settings.py) is
    # an out-of-band shared secret only the operator has, so this is really
    # "provision the account", not "sign up" (found via Codex's Phase 4b
    # pre-push review, round 3). compare_digest avoids leaking the secret's
    # value byte-by-byte through response timing; token_ok is still computed
    # even once any_existing_user is set, for the same reason login always
    # pays bcrypt's cost below — a short-circuit would make "already
    # registered" measurably faster than "wrong secret, first account".
    any_existing_user = (await session.exec(select(User.id).limit(1))).first()
    token_ok = secrets.compare_digest(body.registration_secret, settings.registration_secret)
    if any_existing_user is not None or not token_ok:
        raise HTTPException(403, "Registration is closed — this app supports a single account")
    try:
        # bcrypt is deliberately slow (that's the point of a password hash),
        # which means it blocks the single Uvicorn event loop for the
        # duration if called directly from an async endpoint — including the
        # repo-progress WebSocket other requests may be waiting on
        # concurrently. Offloaded to a worker thread (found via Codex's
        # Phase 4b pre-push review, round 3).
        password_hash = await to_thread(hash_password, body.password)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    user = User(email=body.email, password_hash=password_hash)
    session.add(user)
    await session.flush()  # assigns user.id, needed for the claim below

    # This is the only account that will ever exist (the lockout above
    # guarantees it), so any repo left over from before auth existed —
    # user_id NULL, the nullable-until-Phase-4b default (ADR-007) — is
    # unambiguously this account's. Claiming them here, rather than a
    # one-time data migration, means the invariant holds regardless of
    # exactly when repos were created relative to this deploy (found via
    # Codex's Phase 4b pre-push review: the original plan's Phase 4
    # checklist calls for this backfill, docs/design/original-project-plan.md
    # §12).
    await session.exec(update(Repo).where(Repo.user_id.is_(None)).values(user_id=user.id))
    await session.commit()
    await session.refresh(user)

    raw_token = await create_session(session, user.id)
    _set_session_cookie(response, raw_token)
    return user


@router.post("/login", response_model=UserOut)
async def login(body: LoginRequest, response: Response, session: AsyncSession = Depends(get_session)) -> User:
    user = (await session.exec(select(User).where(User.email == body.email))).first()
    # Same error for "no such user" and "wrong password" — a distinct
    # message would let a caller enumerate registered emails. Timing must
    # match too: `user is None` alone would short-circuit straight past
    # verify_password's real bcrypt cost, making a missing account's 401
    # measurably faster than a wrong-password 401 even with identical
    # response bodies (found via Codex's Phase 4b pre-push review, round 2).
    # Both branches run off the event loop (asyncio.to_thread) — bcrypt is
    # synchronous and deliberately slow, so calling it directly here would
    # stall every other request (including the repo-progress WebSocket) for
    # its duration on every login attempt, not just a rare one (found via
    # Codex's Phase 4b pre-push review, round 3).
    if user is not None:
        password_ok = await to_thread(verify_password, body.password, user.password_hash)
    else:
        await to_thread(verify_password, body.password, _DUMMY_PASSWORD_HASH)  # pays the same cost, result unused
        password_ok = False
    if user is None or not password_ok:
        raise HTTPException(401, "Incorrect email or password")

    raw_token = await create_session(session, user.id)
    _set_session_cookie(response, raw_token)
    return user


@router.post("/logout", status_code=204)
async def logout(
    response: Response,
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    session: AsyncSession = Depends(get_session),
) -> None:
    if session_token is not None:
        await delete_session(session, session_token)
    response.delete_cookie(SESSION_COOKIE_NAME)


async def get_current_user(
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    session: AsyncSession = Depends(get_session),
) -> User:
    if session_token is None:
        raise HTTPException(401, "Not authenticated")
    user = await get_user_from_token(session, session_token)
    if user is None:
        raise HTTPException(401, "Not authenticated")
    return user


@router.get("/me", response_model=UserOut)
async def get_me(current_user: User = Depends(get_current_user)) -> User:
    return current_user
