from fastapi.testclient import TestClient

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


def test_register_duplicate_email_returns_409() -> None:
    with TestClient(app) as client:
        client.post("/auth/register", json={"email": "dup@example.com", "password": "a-real-password"})
        response = client.post("/auth/register", json={"email": "dup@example.com", "password": "different-password"})
    assert response.status_code == 409


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
