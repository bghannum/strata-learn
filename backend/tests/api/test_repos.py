"""Endpoint tests use `with TestClient(app) as client:` deliberately — without
the context manager, each call can spin up a fresh anyio event loop, and our
global asyncpg-backed async engine (created once at import time) then raises
"Future attached to a different loop" on the second call in a test. Entering
the context manager pins one loop for the whole block.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


def test_create_repo_from_git_url(git_fixture_repo: Path) -> None:
    with TestClient(app) as client:
        response = client.post(
            "/repos",
            data={"source_type": "git_url", "git_url": git_fixture_repo.as_uri()},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["source_type"] == "git_url"
        assert body["latest_snapshot_id"] is not None

        snapshot = client.get(f"/repos/{body['id']}/snapshot")
        assert snapshot.status_code == 200
        snap_body = snapshot.json()
        assert snap_body["status"] == "ready"
        assert snap_body["file_count"] == 1
        assert snap_body["language_summary"] == {"python": 1}


def test_create_repo_from_zip_upload(tmp_path: Path) -> None:
    import zipfile

    zip_path = tmp_path / "upload.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("hello.py", "def hello():\n    return 'hi'\n")

    with TestClient(app) as client, open(zip_path, "rb") as f:
        response = client.post(
            "/repos",
            data={"source_type": "zip_upload", "display_name": "zip test"},
            files={"file": ("upload.zip", f, "application/zip")},
        )
    assert response.status_code == 201
    body = response.json()
    assert body["source_type"] == "zip_upload"
    assert body["source_uri"] == "upload.zip"
    assert body["display_name"] == "zip test"


def test_create_repo_bad_git_url_returns_422() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/repos", data={"source_type": "git_url", "git_url": "file:///definitely/does/not/exist"}
        )
    assert response.status_code == 422


def test_create_repo_missing_git_url_returns_422() -> None:
    with TestClient(app) as client:
        response = client.post("/repos", data={"source_type": "git_url"})
    assert response.status_code == 422


def test_create_repo_missing_file_returns_422() -> None:
    with TestClient(app) as client:
        response = client.post("/repos", data={"source_type": "zip_upload"})
    assert response.status_code == 422


def test_list_and_get_repo(git_fixture_repo: Path) -> None:
    with TestClient(app) as client:
        created = client.post(
            "/repos",
            data={"source_type": "git_url", "git_url": git_fixture_repo.as_uri()},
        ).json()

        listed = client.get("/repos")
        assert listed.status_code == 200
        assert any(r["id"] == created["id"] for r in listed.json())

        detail = client.get(f"/repos/{created['id']}")
        assert detail.status_code == 200
        assert detail.json()["id"] == created["id"]


def test_get_repo_not_found_returns_404() -> None:
    with TestClient(app) as client:
        response = client.get("/repos/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_get_snapshot_for_repo_without_one_is_unreachable() -> None:
    # every repo created via POST /repos gets a snapshot synchronously (D13) —
    # there's no code path that creates a repo without one, so 404 is only
    # reachable via a nonexistent repo id
    with TestClient(app) as client:
        response = client.get("/repos/00000000-0000-0000-0000-000000000000/snapshot")
    assert response.status_code == 404
