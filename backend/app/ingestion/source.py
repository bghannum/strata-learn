"""Repo source acquisition: git-clone or zip-upload into a scoped per-job temp
directory. No local-filesystem ingestion option — see ADR-008. This bounds the
trust boundary to a snapshot the app itself created and fully controls, rather
than an arbitrary path the caller points at.
"""

import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import BinaryIO

import git

from app.config import settings


class SourcePreparationError(ValueError):
    """Raised when a source can't be safely prepared: oversized/malicious zip, clone failure."""


# tempfile.gettempdir() resolves to /tmp on the containers this runs in, matching
# PROJECT_PLAN.md §8's /tmp/strata-learn-jobs/{snapshot_id}/ layout.
JOBS_ROOT = Path(tempfile.gettempdir()) / "strata-learn-jobs"


def job_workspace(job_id: uuid.UUID) -> Path:
    workspace = JOBS_ROOT / str(job_id)
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def cleanup_workspace(job_id: uuid.UUID) -> None:
    """Wipe a job's temp directory. Called at the end of the pipeline (§8 step 13) —
    citations capture snippet_text up front specifically so nothing downstream needs
    this directory to still exist."""
    shutil.rmtree(JOBS_ROOT / str(job_id), ignore_errors=True)


def clone_git_repo(url: str, job_id: uuid.UUID) -> tuple[Path, str | None]:
    """Shallow-clone `url` into a scoped job workspace. Returns (source_dir, commit_hash)."""
    source_dir = job_workspace(job_id) / "source"
    try:
        repo = git.Repo.clone_from(url, source_dir, depth=1)
    except git.GitCommandError as exc:
        raise SourcePreparationError(f"Could not clone repository: {exc}") from exc

    try:
        commit_hash = repo.head.commit.hexsha
    except ValueError:
        # empty repo, no commits yet
        commit_hash = None
    return source_dir, commit_hash


def extract_zip_upload(zip_file: BinaryIO, job_id: uuid.UUID) -> Path:
    """Validate then extract a zip upload into a scoped job workspace. Returns source_dir.

    Guards against both a resource-exhaustion upload (too many files / too much
    uncompressed data — a "zip bomb") and zip-slip path traversal, before extracting
    anything, rather than letting tree-sitter parsing silently run for minutes on an
    oversized upload (§8 zip upload guard).
    """
    source_dir = job_workspace(job_id) / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    resolved_dest = source_dir.resolve()

    with zipfile.ZipFile(zip_file) as zf:
        infolist = zf.infolist()
        if len(infolist) > settings.zip_upload_max_files:
            raise SourcePreparationError(
                f"Zip contains {len(infolist)} files, exceeds the "
                f"{settings.zip_upload_max_files}-file limit"
            )

        total_uncompressed = 0
        for info in infolist:
            _validate_zip_member_path(info.filename, resolved_dest)
            total_uncompressed += info.file_size
            if total_uncompressed > settings.zip_upload_max_bytes:
                raise SourcePreparationError(
                    f"Zip contents exceed the {settings.zip_upload_max_bytes}-byte limit"
                )

        zf.extractall(source_dir)

    return source_dir


def _validate_zip_member_path(member_name: str, resolved_dest: Path) -> None:
    """Reject a zip entry whose name would extract outside `resolved_dest` — via an
    absolute path or `../` traversal (zip-slip)."""
    member_path = (resolved_dest / member_name).resolve()
    if resolved_dest != member_path and resolved_dest not in member_path.parents:
        raise SourcePreparationError(f"Zip entry escapes extraction directory: {member_name!r}")
