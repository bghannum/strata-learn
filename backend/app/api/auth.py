"""POST /auth/register, /auth/login, /auth/logout, GET /auth/me — session
issuance/validation for the self-implemented, DB-backed auth design (ADR-007,
app/auth/session.py). Every issuing endpoint sets the session cookie itself
directly on the Response rather than returning a token in the body — an
HttpOnly cookie can't be read or exfiltrated by JS, which is the whole point.

get_current_user is the dependency other routers (api/repos.py,
api/study_guides.py) import to scope requests to the logged-in user.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, EmailStr
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.security import hash_password, verify_password
from app.auth.session import SESSION_LIFETIME, create_session, delete_session, get_user_from_token
from app.db.models import User
from app.db.session import get_session

router = APIRouter(prefix="/auth", tags=["auth"])

SESSION_COOKIE_NAME = "session_token"


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


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
    # Checked first, before the single-tenant lockout below: if the caller's
    # own email is the one already registered, "an account already exists"
    # is more useful than a generic lockout message — it points at logging
    # in instead. A truly new email past this point means the caller isn't
    # the account holder, so the lockout applies to it.
    existing = (await session.exec(select(User).where(User.email == body.email))).first()
    if existing is not None:
        raise HTTPException(409, "An account with this email already exists")

    # ADR-007: this is a single-tenant app by design, not a general-purpose
    # signup surface — an unrestricted register endpoint would let anyone who
    # can reach the API create accounts that each enqueue real, paid
    # indexing/LLM work, which is a real exposure once this is ever deployed
    # beyond localhost (the original plan's own Phase 7 goal). The first
    # account ever created is the only one allowed; found via Codex's Phase
    # 4b pre-push review.
    any_existing_user = (await session.exec(select(User.id).limit(1))).first()
    if any_existing_user is not None:
        raise HTTPException(403, "Registration is closed — this app supports a single account")
    try:
        password_hash = hash_password(body.password)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    user = User(email=body.email, password_hash=password_hash)
    session.add(user)
    await session.commit()
    await session.refresh(user)

    raw_token = await create_session(session, user.id)
    _set_session_cookie(response, raw_token)
    return user


@router.post("/login", response_model=UserOut)
async def login(body: LoginRequest, response: Response, session: AsyncSession = Depends(get_session)) -> User:
    user = (await session.exec(select(User).where(User.email == body.email))).first()
    # Same error for "no such user" and "wrong password" — a distinct message
    # would let a caller enumerate registered emails.
    if user is None or not verify_password(body.password, user.password_hash):
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
