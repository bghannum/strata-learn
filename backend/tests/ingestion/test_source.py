import io
import subprocess
import uuid
import zipfile
from pathlib import Path

import pytest

from app.config import settings
from app.ingestion.source import (
    SourcePreparationError,
    cleanup_workspace,
    clone_git_repo,
    extract_zip_upload,
)


def test_clone_git_repo_success(git_fixture_repo: Path) -> None:
    job_id = uuid.uuid4()
    try:
        source_dir, commit_hash = clone_git_repo(git_fixture_repo.as_uri(), job_id)
        assert (source_dir / "app.py").exists()
        assert commit_hash is not None
    finally:
        cleanup_workspace(job_id)


def test_clone_git_repo_bad_url_raises() -> None:
    job_id = uuid.uuid4()
    try:
        with pytest.raises(SourcePreparationError):
            clone_git_repo("file:///definitely/does/not/exist", job_id)
    finally:
        cleanup_workspace(job_id)


def test_clone_git_repo_pinned_commit_ignores_later_tip(tmp_path: Path) -> None:
    # A resumed study guide generation (worker/pipeline.py) must reacquire
    # the exact commit Layer A/B data was persisted against, not whatever
    # the branch has since advanced to.
    repo_dir = tmp_path / "multi-commit-repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_dir, check=True)
    (repo_dir / "file.txt").write_text("v1\n")
    subprocess.run(["git", "add", "file.txt"], cwd=repo_dir, check=True)
    subprocess.run(
        ["git", "-c", "user.email=test@test.com", "-c", "user.name=test", "commit", "-q", "-m", "v1"],
        cwd=repo_dir,
        check=True,
    )
    first_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_dir, check=True, capture_output=True, text=True
    ).stdout.strip()

    (repo_dir / "file.txt").write_text("v2\n")
    subprocess.run(["git", "add", "file.txt"], cwd=repo_dir, check=True)
    subprocess.run(
        ["git", "-c", "user.email=test@test.com", "-c", "user.name=test", "commit", "-q", "-m", "v2"],
        cwd=repo_dir,
        check=True,
    )

    job_id = uuid.uuid4()
    try:
        source_dir, commit_hash = clone_git_repo(repo_dir.as_uri(), job_id, pinned_commit=first_sha)
        assert commit_hash == first_sha
        assert (source_dir / "file.txt").read_text() == "v1\n"
    finally:
        cleanup_workspace(job_id)


def test_cleanup_workspace_removes_directory(git_fixture_repo: Path) -> None:
    job_id = uuid.uuid4()
    source_dir, _ = clone_git_repo(git_fixture_repo.as_uri(), job_id)
    assert source_dir.exists()
    cleanup_workspace(job_id)
    assert not source_dir.exists()


def _make_zip(entries: dict[str, str]) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    buf.seek(0)
    return buf


def test_extract_zip_upload_success() -> None:
    job_id = uuid.uuid4()
    try:
        zip_bytes = _make_zip({"app.py": "print(1)", "sub/util.py": "x = 1"})
        dest = extract_zip_upload(zip_bytes, job_id)
        assert (dest / "app.py").exists()
        assert (dest / "sub" / "util.py").exists()
    finally:
        cleanup_workspace(job_id)


def test_extract_zip_upload_rejects_zip_slip() -> None:
    job_id = uuid.uuid4()
    try:
        zip_bytes = _make_zip({"../../evil.py": "pwn"})
        with pytest.raises(SourcePreparationError, match="escapes extraction directory"):
            extract_zip_upload(zip_bytes, job_id)
    finally:
        cleanup_workspace(job_id)


def test_extract_zip_upload_rejects_too_many_files(monkeypatch) -> None:
    monkeypatch.setattr(settings, "zip_upload_max_files", 2)
    job_id = uuid.uuid4()
    try:
        zip_bytes = _make_zip({f"f{i}.py": "x" for i in range(5)})
        with pytest.raises(SourcePreparationError, match="file limit|exceeds"):
            extract_zip_upload(zip_bytes, job_id)
    finally:
        cleanup_workspace(job_id)


def test_extract_zip_upload_rejects_oversized_content(monkeypatch) -> None:
    monkeypatch.setattr(settings, "zip_upload_max_bytes", 10)
    job_id = uuid.uuid4()
    try:
        zip_bytes = _make_zip({"big.py": "x" * 1000})
        with pytest.raises(SourcePreparationError, match="byte limit|exceeds"):
            extract_zip_upload(zip_bytes, job_id)
    finally:
        cleanup_workspace(job_id)
