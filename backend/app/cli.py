"""Operator commands that don't belong behind the HTTP API.

    python -m app.cli reset-password you@example.com

The app is single-tenant with no email delivery, so there's deliberately no
"forgot password" flow in the UI: nothing could prove it's you asking. Shell
access to the box (or `docker compose exec api`) *is* that proof, so the
recovery path lives here — the same reasoning that puts REGISTRATION_SECRET
out of band (config.py). Before this existed, forgetting the one password
meant wiping the database, which took every indexed repo, guide, and quiz
attempt with it. `scripts/reset-password` at the repo root wraps this for
the Docker workflow.
"""

import argparse
import asyncio
import getpass
import sys

from sqlmodel import delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.security import hash_password
from app.db.models import Session, User


async def reset_password(session: AsyncSession, email: str, new_password: str) -> None:
    """Replaces the account's password and ends every open session for it —
    a reset that left existing sessions valid wouldn't lock out whoever
    prompted the reset in the first place. Raises LookupError for an
    unknown email and ValueError (from hash_password) for an unusable
    password; commits on success."""
    user = (await session.exec(select(User).where(User.email == email))).first()
    if user is None:
        raise LookupError(f"No account with email {email!r}")
    user.password_hash = hash_password(new_password)
    session.add(user)
    await session.exec(delete(Session).where(Session.user_id == user.id))
    await session.commit()


async def _run_reset_password(email: str, new_password: str) -> None:
    from app.db.session import async_session_factory  # deferred: engine construction reads settings

    async with async_session_factory() as session:
        await reset_password(session, email, new_password)


def _read_new_password() -> str:
    first = getpass.getpass("New password: ")
    second = getpass.getpass("Repeat new password: ")
    if first != second:
        raise SystemExit("reset-password: passwords did not match")
    if not first:
        raise SystemExit("reset-password: password must not be empty")
    return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__.split("\n\n")[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    reset = subparsers.add_parser("reset-password", help="Set a new password for the account and end its sessions.")
    reset.add_argument("email")
    reset.add_argument(
        "--password",
        help="Non-interactive use only (it lands in shell history); prompts securely when omitted.",
    )

    args = parser.parse_args(argv)
    if args.command == "reset-password":
        new_password = args.password if args.password is not None else _read_new_password()
        try:
            asyncio.run(_run_reset_password(args.email, new_password))
        except (LookupError, ValueError) as exc:
            print(f"reset-password: {exc}", file=sys.stderr)
            return 1
        print(f"Password updated for {args.email}; all sessions for that account have been signed out.")
        return 0
    return 2  # unreachable: argparse rejects unknown commands


if __name__ == "__main__":
    raise SystemExit(main())
