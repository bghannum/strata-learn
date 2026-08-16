from fastapi.testclient import TestClient

import pytest

from app.config import settings
from app.db.models import SourceType
from app.main import app

# The tests below register with no secret: settings.registration_secret is
# blank by default (config.py), which is the localhost / out-of-the-box mode.
# The secret-gated mode is exercised explicitly by the tests that use the
# `secret_required` fixture.


@pytest.fixture
def secret_required(monkeypatch) -> str:
    monkeypatch.setattr(settings, "registration_secret", "operator-only-secret")
    return "operator-only-secret"


def test_register_sets_session_cookie_and_returns_user_without_password_hash() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/auth/register",
            json={
                "email": "new@example.com",
                "password": "a-real-password",
            },
        )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "new@example.com"
    assert "password_hash" not in body
    assert "password" not in body
    assert "session_token" in response.cookies


def test_register_after_lockout_returns_the_same_response_regardless_of_email() -> None:
    # ADR-007: single-tenant by design — an open registration endpoint would
    # let anyone who can reach the API create accounts that each enqueue
    # real, paid indexing/LLM work. Only the first account ever created is
    # allowed, and — since the caller isn't authenticated yet — a repeat of
    # the *same* email gets the identical response as a genuinely *different*
    # one: distinguishing them would let an unauthenticated caller use
    # registration to enumerate whether a given email is the registered
    # account (found via Codex's Phase 4b pre-push review, round 2).
    with TestClient(app) as client:
        client.post(
            "/auth/register",
            json={"email": "first@example.com", "password": "a-real-password"},
        )

        same_email = client.post(
            "/auth/register",
            json={"email": "first@example.com", "password": "whatever"},
        )
        different_email = client.post(
            "/auth/register",
            json={"email": "second@example.com", "password": "whatever"},
        )

    assert same_email.status_code == 403
    assert different_email.status_code == 403
    assert same_email.json() == different_email.json()


def test_register_with_wrong_secret_is_rejected_even_for_the_first_account(secret_required) -> None:
    # The single-tenant lockout (see the test above) only closes registration
    # *after* an account exists — on a freshly reachable deployment, that
    # leaves a race where whoever hits this endpoint first, not necessarily
    # the operator, permanently owns the app. When REGISTRATION_SECRET is
    # set it closes that gap: it's required even when no account exists yet,
    # and a wrong (or missing) secret gets the identical 403 as "already
    # registered" so it isn't a way to probe whether the app has been
    # provisioned (found via Codex's Phase 4b pre-push review, round 3).
    with TestClient(app) as client:
        wrong = client.post(
            "/auth/register",
            json={"email": "attacker@example.com", "password": "whatever", "registration_secret": "wrong-secret"},
        )
        missing = client.post("/auth/register", json={"email": "attacker@example.com", "password": "whatever"})
        assert wrong.status_code == 403
        assert missing.status_code == 403
        assert wrong.json() == missing.json()

        me = client.get("/auth/me")

    assert me.status_code == 401  # definitely didn't get a session


def test_register_with_the_right_secret_succeeds_when_one_is_required(secret_required) -> None:
    with TestClient(app) as client:
        response = client.post(
            "/auth/register",
            json={"email": "operator@example.com", "password": "a-real-password", "registration_secret": secret_required},
        )
        assert response.status_code == 201
        assert client.get("/auth/me").status_code == 200


def test_register_ignores_a_submitted_secret_when_none_is_required() -> None:
    # Blank REGISTRATION_SECRET (the default) means "not checked", not
    # "must be blank" — an operator who clears the setting after handing
    # out a value shouldn't strand a client that still sends it.
    with TestClient(app) as client:
        response = client.post(
            "/auth/register",
            json={"email": "local@example.com", "password": "a-real-password", "registration_secret": "stale-value"},
        )
    assert response.status_code == 201


def test_status_reports_setup_required_until_the_account_exists() -> None:
    # This is what routes a fresh install to the setup screen instead of a
    # login form nobody can get past. Unauthenticated on purpose — there is
    # no one to authenticate yet when it matters most.
    with TestClient(app) as client:
        before = client.get("/auth/status")
        assert before.status_code == 200
        assert before.json() == {"setup_required": True, "secret_required": False}

        client.post("/auth/register", json={"email": "first@example.com", "password": "a-real-password"})
        client.cookies.clear()

        after = client.get("/auth/status")

    assert after.json() == {"setup_required": False, "secret_required": False}


def test_status_reports_whether_the_setup_form_must_ask_for_the_secret(secret_required) -> None:
    with TestClient(app) as client:
        response = client.get("/auth/status")
    assert response.json() == {"setup_required": True, "secret_required": True}


async def test_register_claims_repos_left_over_from_before_auth_existed(pending_repo_factory) -> None:
    # Every repo created before Phase 4b has user_id=NULL (the
    # nullable-until-auth default — ADR-007). Since this account is
    # guaranteed to be the only one that will ever exist, any such repo is
    # unambiguously its owner's — claimed here rather than via a one-time
    # data migration (found via Codex's Phase 4b pre-push review, round 2;
    # the original plan's own Phase 4 checklist calls for this backfill).
    orphaned_repo_id, _snapshot_id = await pending_repo_factory(SourceType.git_url, "https://example.com/repo.git")

    with TestClient(app) as client:
        client.post(
            "/auth/register",
            json={
                "email": "claims-orphans@example.com",
                "password": "a-real-password",
            },
        )
        response = client.get(f"/repos/{orphaned_repo_id}")

    assert response.status_code == 200
    assert response.json()["id"] == str(orphaned_repo_id)


def test_register_rejects_invalid_email() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/auth/register",
            json={
                "email": "not-an-email",
                "password": "a-real-password",
            },
        )
    assert response.status_code == 422


def test_login_with_correct_credentials_sets_session_cookie() -> None:
    with TestClient(app) as client:
        client.post(
            "/auth/register",
            json={
                "email": "login-test@example.com",
                "password": "a-real-password",
            },
        )
        client.cookies.clear()  # simulate a fresh browser session, no leftover cookie from register

        response = client.post(
            "/auth/login", json={"email": "login-test@example.com", "password": "a-real-password"}
        )

    assert response.status_code == 200
    assert response.json()["email"] == "login-test@example.com"
    assert "session_token" in response.cookies


def test_login_with_wrong_password_returns_401() -> None:
    with TestClient(app) as client:
        client.post(
            "/auth/register",
            json={
                "email": "wrong-pw@example.com",
                "password": "a-real-password",
            },
        )
        client.cookies.clear()

        response = client.post("/auth/login", json={"email": "wrong-pw@example.com", "password": "nope"})

    assert response.status_code == 401


def test_login_with_unknown_email_returns_401() -> None:
    with TestClient(app) as client:
        response = client.post("/auth/login", json={"email": "nobody@example.com", "password": "whatever"})
    assert response.status_code == 401


def test_login_with_unknown_email_still_pays_bcrypt_cost(monkeypatch) -> None:
    # A missing user must not short-circuit past verify_password — otherwise
    # "no such account" is measurably faster than "wrong password", a timing
    # side-channel even though both return the identical 401 body (found via
    # Codex's Phase 4b pre-push review, round 2). Also confirms the call
    # actually runs off the main thread (asyncio.to_thread) — bcrypt is
    # synchronous and deliberately slow, so calling it directly would stall
    # the event loop, including the repo-progress WebSocket, for its
    # duration on every login attempt (found via Codex's Phase 4b pre-push
    # review, round 3).
    import threading

    import app.api.auth as auth_module

    calling_threads: list[threading.Thread] = []
    calls: list[str] = []
    original = auth_module.verify_password

    def _spy(password: str, password_hash: str) -> bool:
        calling_threads.append(threading.current_thread())
        calls.append(password_hash)
        return original(password, password_hash)

    monkeypatch.setattr(auth_module, "verify_password", _spy)

    with TestClient(app) as client:
        response = client.post("/auth/login", json={"email": "nobody-at-all@example.com", "password": "whatever"})

    assert response.status_code == 401
    assert calls == [auth_module._DUMMY_PASSWORD_HASH]
    assert calling_threads[0] is not threading.current_thread()


def test_register_hashes_the_password_off_the_event_loop(monkeypatch) -> None:
    # Same reasoning as the login test above, for the register path's own
    # bcrypt call (found via Codex's Phase 4b pre-push review, round 3).
    import threading

    import app.api.auth as auth_module

    calling_threads: list[threading.Thread] = []
    original = auth_module.hash_password

    def _spy(password: str) -> str:
        calling_threads.append(threading.current_thread())
        return original(password)

    monkeypatch.setattr(auth_module, "hash_password", _spy)

    with TestClient(app) as client:
        response = client.post(
            "/auth/register",
            json={
                "email": "thread-check@example.com",
                "password": "a-real-password",
            },
        )

    assert response.status_code == 201
    assert calling_threads[0] is not threading.current_thread()


def test_me_without_a_session_returns_401() -> None:
    with TestClient(app) as client:
        response = client.get("/auth/me")
    assert response.status_code == 401


def test_me_with_garbage_cookie_returns_401() -> None:
    with TestClient(app) as client:
        client.cookies.set("session_token", "garbage-value-not-a-real-token")
        response = client.get("/auth/me")
    assert response.status_code == 401


def test_logout_clears_the_cookie_and_invalidates_the_session() -> None:
    with TestClient(app) as client:
        client.post(
            "/auth/register",
            json={
                "email": "logout-test@example.com",
                "password": "a-real-password",
            },
        )
        assert client.get("/auth/me").status_code == 200

        logout_response = client.post("/auth/logout")
        assert logout_response.status_code == 204

        assert client.get("/auth/me").status_code == 401


def test_logout_without_a_session_is_a_no_op() -> None:
    with TestClient(app) as client:
        response = client.post("/auth/logout")
    assert response.status_code == 204
