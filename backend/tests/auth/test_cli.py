"""app/cli.py — the operator's password-recovery path. There is deliberately
no "forgot password" over HTTP (nothing could prove it's the operator
asking), so this is the only way back in short of wiping the database."""

import pytest
from fastapi.testclient import TestClient

from app import cli
from app.db.session import async_session_factory
from app.main import app


def _register(client: TestClient, email: str, password: str) -> None:
    response = client.post("/auth/register", json={"email": email, "password": password})
    assert response.status_code == 201, response.text


async def test_reset_password_changes_the_password_and_signs_out_open_sessions() -> None:
    with TestClient(app) as client:
        _register(client, "operator@example.com", "old-password")
        assert client.get("/auth/me").status_code == 200

        async with async_session_factory() as session:
            await cli.reset_password(session, "operator@example.com", "new-password")

        # The session that existed before the reset is gone — a reset that
        # left it valid wouldn't lock out whoever prompted the reset.
        assert client.get("/auth/me").status_code == 401

        old = client.post("/auth/login", json={"email": "operator@example.com", "password": "old-password"})
        new = client.post("/auth/login", json={"email": "operator@example.com", "password": "new-password"})

    assert old.status_code == 401
    assert new.status_code == 200


async def test_reset_password_for_an_unknown_email_changes_nothing() -> None:
    with TestClient(app) as client:
        _register(client, "operator@example.com", "old-password")

        async with async_session_factory() as session:
            with pytest.raises(LookupError):
                await cli.reset_password(session, "nobody@example.com", "new-password")

        # Untouched: the existing session and the old password still work.
        assert client.get("/auth/me").status_code == 200
        client.cookies.clear()
        response = client.post("/auth/login", json={"email": "operator@example.com", "password": "old-password"})

    assert response.status_code == 200


def test_main_reports_an_unknown_email_on_stderr_and_exits_nonzero(capsys) -> None:
    exit_code = cli.main(["reset-password", "nobody@example.com", "--password", "whatever"])

    assert exit_code == 1
    assert "No account with email 'nobody@example.com'" in capsys.readouterr().err


def test_main_resets_the_password_end_to_end(capsys) -> None:
    with TestClient(app) as client:
        _register(client, "operator@example.com", "old-password")
        client.cookies.clear()

        exit_code = cli.main(["reset-password", "operator@example.com", "--password", "new-password"])

        response = client.post("/auth/login", json={"email": "operator@example.com", "password": "new-password"})

    assert exit_code == 0
    assert "Password updated for operator@example.com" in capsys.readouterr().out
    assert response.status_code == 200
