from fastapi.testclient import TestClient

from app.db.models import SourceType
from app.main import app


def test_register_sets_session_cookie_and_returns_user_without_password_hash() -> None:
    with TestClient(app) as client:
        response = client.post("/auth/register", json={"email": "new@example.com", "password": "a-real-password"})

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
        client.post("/auth/register", json={"email": "first@example.com", "password": "a-real-password"})

        same_email = client.post("/auth/register", json={"email": "first@example.com", "password": "whatever"})
        different_email = client.post("/auth/register", json={"email": "second@example.com", "password": "whatever"})

    assert same_email.status_code == 403
    assert different_email.status_code == 403
    assert same_email.json() == different_email.json()


async def test_register_claims_repos_left_over_from_before_auth_existed(pending_repo_factory) -> None:
    # Every repo created before Phase 4b has user_id=NULL (the
    # nullable-until-auth default — ADR-007). Since this account is
    # guaranteed to be the only one that will ever exist, any such repo is
    # unambiguously its owner's — claimed here rather than via a one-time
    # data migration (found via Codex's Phase 4b pre-push review, round 2;
    # the original plan's own Phase 4 checklist calls for this backfill).
    orphaned_repo_id, _snapshot_id = await pending_repo_factory(SourceType.git_url, "https://example.com/repo.git")

    with TestClient(app) as client:
        client.post("/auth/register", json={"email": "claims-orphans@example.com", "password": "a-real-password"})
        response = client.get(f"/repos/{orphaned_repo_id}")

    assert response.status_code == 200
    assert response.json()["id"] == str(orphaned_repo_id)


def test_register_rejects_invalid_email() -> None:
    with TestClient(app) as client:
        response = client.post("/auth/register", json={"email": "not-an-email", "password": "a-real-password"})
    assert response.status_code == 422


def test_login_with_correct_credentials_sets_session_cookie() -> None:
    with TestClient(app) as client:
        client.post("/auth/register", json={"email": "login-test@example.com", "password": "a-real-password"})
        client.cookies.clear()  # simulate a fresh browser session, no leftover cookie from register

        response = client.post(
            "/auth/login", json={"email": "login-test@example.com", "password": "a-real-password"}
        )

    assert response.status_code == 200
    assert response.json()["email"] == "login-test@example.com"
    assert "session_token" in response.cookies


def test_login_with_wrong_password_returns_401() -> None:
    with TestClient(app) as client:
        client.post("/auth/register", json={"email": "wrong-pw@example.com", "password": "a-real-password"})
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
    # Codex's Phase 4b pre-push review, round 2).
    import app.api.auth as auth_module

    calls: list[str] = []
    original = auth_module.verify_password

    def _spy(password: str, password_hash: str) -> bool:
        calls.append(password_hash)
        return original(password, password_hash)

    monkeypatch.setattr(auth_module, "verify_password", _spy)

    with TestClient(app) as client:
        response = client.post("/auth/login", json={"email": "nobody-at-all@example.com", "password": "whatever"})

    assert response.status_code == 401
    assert calls == [auth_module._DUMMY_PASSWORD_HASH]


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
        client.post("/auth/register", json={"email": "logout-test@example.com", "password": "a-real-password"})
        assert client.get("/auth/me").status_code == 200

        logout_response = client.post("/auth/logout")
        assert logout_response.status_code == 204

        assert client.get("/auth/me").status_code == 401


def test_logout_without_a_session_is_a_no_op() -> None:
    with TestClient(app) as client:
        response = client.post("/auth/logout")
    assert response.status_code == 204
