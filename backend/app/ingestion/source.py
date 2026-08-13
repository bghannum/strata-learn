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
# docs/design/original-project-plan.md §8's /tmp/strata-learn-jobs/{snapshot_id}/ layout.
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


def check_git_url_reachable(url: str, timeout_seconds: float = 10) -> None:
    """Cheap synchronous pre-check before enqueueing a clone job — `git ls-remote`
    talks to the remote without fetching any objects, so an unreachable host or
    nonexistent repo fails fast (422) at request time instead of only surfacing
    as an async `status=failed` after the worker picks up the job.

    kill_after_timeout bounds the actual subprocess, not just the coroutine
    awaiting it — an outer asyncio-level timeout alone (e.g. asyncio.wait_for
    around asyncio.to_thread) can't stop a hung `git` process; it only stops
    the caller from waiting on it, leaving the process and its thread running
    indefinitely. Confirmed empirically: a hung remote gets SIGKILLed within
    ~timeout_seconds and raises GitCommandError, not a bare hang."""
    try:
        git.cmd.Git().ls_remote(url, kill_after_timeout=timeout_seconds)
    except git.GitCommandError as exc:
        raise SourcePreparationError(f"Could not reach repository: {exc}") from exc


def clone_git_repo(url: str, job_id: uuid.UUID, pinned_commit: str | None = None) -> tuple[Path, str | None]:
    """Shallow-clone `url` into a scoped job workspace. Returns (source_dir, commit_hash).

    `pinned_commit`, when given, fetches and checks out that exact commit
    instead of the branch tip — used when worker/pipeline.py resumes study
    guide generation after a crash: the persisted Layer A/B data (line
    ranges, evidence, citations) describes the commit originally analyzed,
    not whatever the tracked branch has since advanced to, so re-acquiring
    source for citation snippet capture must reacquire that exact commit —
    a fresh tip clone could disagree with already-persisted line numbers or
    no longer contain a since-deleted file (found via Codex's Phase 3
    pre-push review). Requires the remote to allow fetching a reachable SHA
    directly (`uploadpack.allowReachableSHA1InWant`) — true for GitHub/
    GitLab and for the local file:// remotes this test suite uses; an
    origin that rejects it surfaces as a clear SourcePreparationError, not
    a silent fall-back to the wrong commit.
    """
    source_dir = job_workspace(job_id) / "source"
    try:
        if pinned_commit is not None:
            repo = git.Repo.init(source_dir)
            origin = repo.create_remote("origin", url)
            origin.fetch(pinned_commit, depth=1)
            repo.git.checkout(pinned_commit)
        else:
            repo = git.Repo.clone_from(url, source_dir, depth=1)
    except git.GitCommandError as exc:
        raise SourcePreparationError(f"Could not clone repository: {exc}") from exc

    try:
        commit_hash = repo.head.commit.hexsha
    except ValueError:
        # empty repo, no commits yet
        commit_hash = None
    return source_dir, commit_hash


def validate_zip_upload(zip_file: BinaryIO, resolved_dest: Path | None = None) -> None:
    """Guards against a resource-exhaustion upload (too many files / too much
    uncompressed data — a "zip bomb") and zip-slip path traversal, without
    extracting anything — only reads the central directory, so this is cheap
    enough to run synchronously at request time (before enqueueing the actual
    extraction job) as well as again in the worker before extracting.

    `resolved_dest` is only needed to validate member paths against a real
    extraction target; omit it for a pre-check that doesn't have one yet (zip-slip
    membership is still checked against a throwaway root, so traversal is still
    caught, just not tied to the eventual real destination).
    """
    # .resolve() unconditionally, even when the caller already passed a
    # resolved path — the fallback specifically needs it: tempfile.gettempdir()
    # returns an unresolved path (/tmp on macOS is a symlink to /private/tmp),
    # so comparing it as-is against a member path that .resolve() has already
    # expanded made every valid entry look like it "escaped" the directory.
    dest = (resolved_dest or Path(tempfile.gettempdir())).resolve()

    with zipfile.ZipFile(zip_file) as zf:
        infolist = zf.infolist()
        if len(infolist) > settings.zip_upload_max_files:
            raise SourcePreparationError(
                f"Zip contains {len(infolist)} files, exceeds the "
                f"{settings.zip_upload_max_files}-file limit"
            )

        total_uncompressed = 0
        for info in infolist:
            _validate_zip_member_path(info.filename, dest)
            total_uncompressed += info.file_size
            if total_uncompressed > settings.zip_upload_max_bytes:
                raise SourcePreparationError(
                    f"Zip contents exceed the {settings.zip_upload_max_bytes}-byte limit"
                )


def extract_zip_upload(zip_file: BinaryIO, job_id: uuid.UUID) -> Path:
    """Validate then extract a zip upload into a scoped job workspace. Returns source_dir.

    Re-validates even if the caller already called `validate_zip_upload` — cheap,
    and this function needs to be safe to call on its own (the worker's only
    guard against a corrupted/tampered Redis-stored upload)."""
    source_dir = job_workspace(job_id) / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    resolved_dest = source_dir.resolve()

    validate_zip_upload(zip_file, resolved_dest)
    zip_file.seek(0)
    with zipfile.ZipFile(zip_file) as zf:
        zf.extractall(source_dir)

    return source_dir


def _validate_zip_member_path(member_name: str, resolved_dest: Path) -> None:
    """Reject a zip entry whose name would extract outside `resolved_dest` — via an
    absolute path or `../` traversal (zip-slip)."""
    member_path = (resolved_dest / member_name).resolve()
    if resolved_dest != member_path and resolved_dest not in member_path.parents:
        raise SourcePreparationError(f"Zip entry escapes extraction directory: {member_name!r}")
