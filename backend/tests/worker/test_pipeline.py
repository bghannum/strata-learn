"""index_repo runs against real Postgres + Redis (no mocking — consistent with
this project's testing philosophy, see tests/conftest.py::clean_db). Exercises
the worker function directly rather than through a running arq worker process
— pytest doesn't have one, and testing the job function's own logic is the
actual unit of behavior that matters here.
"""

import io
import json
import zipfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import UUID

import pytest
from arq.connections import ArqRedis

import app.worker.pipeline as pipeline_module
from app.db.models import AnalysisSnapshot, Repo, SnapshotStatus, SourceType
from app.db.session import async_session_factory
from app.worker.pipeline import index_repo, progress_channel


async def _get_snapshot(snapshot_id: UUID) -> AnalysisSnapshot:
    async with async_session_factory() as session:
        snapshot = await session.get(AnalysisSnapshot, snapshot_id)
        assert snapshot is not None
        return snapshot


async def _collect_published(
    redis_pool: ArqRedis, channel: str, run: Callable[[], Awaitable[None]]
) -> list[dict]:
    pubsub = redis_pool.pubsub()
    await pubsub.subscribe(channel)
    await pubsub.get_message(timeout=1)  # discard the subscribe confirmation itself

    await run()

    messages = []
    while True:
        msg = await pubsub.get_message(timeout=1)
        if msg is None:
            break
        messages.append(json.loads(msg["data"]))
    await pubsub.unsubscribe()
    await pubsub.aclose()
    return messages


async def test_index_repo_git_url_success(
    redis_pool: ArqRedis, git_fixture_repo: Path, pending_repo_factory
) -> None:
    git_url = git_fixture_repo.as_uri()
    repo_id, snapshot_id = await pending_repo_factory(SourceType.git_url, git_url)

    async def run() -> None:
        await index_repo(
            {"redis": redis_pool},
            snapshot_id=snapshot_id,
            repo_id=repo_id,
            source_type=SourceType.git_url.value,
            git_url=git_url,
        )

    messages = await _collect_published(redis_pool, progress_channel(snapshot_id), run)
    assert [m["status"] for m in messages] == [SnapshotStatus.parsing.value, SnapshotStatus.ready.value]

    snapshot = await _get_snapshot(snapshot_id)
    assert snapshot.status == SnapshotStatus.ready
    assert snapshot.file_count == 1
    assert snapshot.commit_hash is not None


async def test_index_repo_zip_upload_success(redis_pool: ArqRedis, pending_repo_factory) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hello.py", "def hello():\n    return 'hi'\n")

    repo_id, snapshot_id = await pending_repo_factory(SourceType.zip_upload, "upload.zip")
    zip_redis_key = f"zip-upload:{snapshot_id}"
    await redis_pool.set(zip_redis_key, buf.getvalue())

    async def run() -> None:
        await index_repo(
            {"redis": redis_pool},
            snapshot_id=snapshot_id,
            repo_id=repo_id,
            source_type=SourceType.zip_upload.value,
            zip_redis_key=zip_redis_key,
        )

    messages = await _collect_published(redis_pool, progress_channel(snapshot_id), run)
    assert [m["status"] for m in messages] == [SnapshotStatus.parsing.value, SnapshotStatus.ready.value]

    snapshot = await _get_snapshot(snapshot_id)
    assert snapshot.status == SnapshotStatus.ready
    assert snapshot.file_count == 1
    # Deliberately NOT deleted on success either — a worker crash between
    # this commit and arq recording the success can redeliver the job, and a
    # retry needs the source data too. Cleanup is the TTL set in api/repos.py
    # alone, on purpose (see worker/pipeline.py's module docstring).
    assert await redis_pool.get(zip_redis_key) == buf.getvalue()


async def test_index_repo_bad_git_url_marks_failed(redis_pool: ArqRedis, pending_repo_factory) -> None:
    bad_url = "file:///definitely/does/not/exist"
    repo_id, snapshot_id = await pending_repo_factory(SourceType.git_url, bad_url)

    async def run() -> None:
        await index_repo(
            {"redis": redis_pool},
            snapshot_id=snapshot_id,
            repo_id=repo_id,
            source_type=SourceType.git_url.value,
            git_url=bad_url,
        )

    messages = await _collect_published(redis_pool, progress_channel(snapshot_id), run)
    assert [m["status"] for m in messages] == [SnapshotStatus.parsing.value, SnapshotStatus.failed.value]
    assert "error" in messages[-1]

    snapshot = await _get_snapshot(snapshot_id)
    assert snapshot.status == SnapshotStatus.failed


async def test_index_repo_unexpected_exception_marks_failed_and_reraises(
    redis_pool: ArqRedis, git_fixture_repo: Path, pending_repo_factory, monkeypatch
) -> None:
    # Before the fix, only SourcePreparationError was caught — a crash from
    # anywhere else (parser bug, unexpected DB failure, ...) propagated with
    # no fail_snapshot/publish ever happening, leaving the snapshot at
    # "parsing" forever and every WS client watching it hanging indefinitely.
    def _boom(source_dir):
        raise RuntimeError("simulated parser crash")

    monkeypatch.setattr(pipeline_module, "analyze_source", _boom)

    git_url = git_fixture_repo.as_uri()
    repo_id, snapshot_id = await pending_repo_factory(SourceType.git_url, git_url)

    async def run() -> None:
        with pytest.raises(RuntimeError, match="simulated parser crash"):
            await index_repo(
                {"redis": redis_pool},
                snapshot_id=snapshot_id,
                repo_id=repo_id,
                source_type=SourceType.git_url.value,
                git_url=git_url,
            )

    messages = await _collect_published(redis_pool, progress_channel(snapshot_id), run)
    assert [m["status"] for m in messages] == [SnapshotStatus.parsing.value, SnapshotStatus.failed.value]
    assert messages[-1]["error"] == "Indexing failed unexpectedly"

    snapshot = await _get_snapshot(snapshot_id)
    assert snapshot.status == SnapshotStatus.failed


async def test_index_repo_zip_upload_unexpected_exception_preserves_zip_key(
    redis_pool: ArqRedis, pending_repo_factory, monkeypatch
) -> None:
    # Complements the test above: for zip uploads specifically, the source
    # data only ever lives in Redis (no re-fetchable URL like git_url has),
    # so deleting it on a path arq might redeliver (a job timeout/cancellation
    # reaches this same finally as a BaseException, and arq's own retry_jobs
    # logic can redeliver that) would make that redelivery unrecoverable.
    def _boom(source_dir):
        raise RuntimeError("simulated parser crash")

    monkeypatch.setattr(pipeline_module, "analyze_source", _boom)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hello.py", "def hello():\n    return 'hi'\n")
    zip_bytes = buf.getvalue()

    repo_id, snapshot_id = await pending_repo_factory(SourceType.zip_upload, "upload.zip")
    zip_redis_key = f"zip-upload:{snapshot_id}"
    await redis_pool.set(zip_redis_key, zip_bytes)

    with pytest.raises(RuntimeError, match="simulated parser crash"):
        await index_repo(
            {"redis": redis_pool},
            snapshot_id=snapshot_id,
            repo_id=repo_id,
            source_type=SourceType.zip_upload.value,
            zip_redis_key=zip_redis_key,
        )

    assert await redis_pool.get(zip_redis_key) == zip_bytes  # preserved for a possible redelivery


async def test_index_repo_tolerates_snapshot_deleted_mid_job(
    redis_pool: ArqRedis, git_fixture_repo: Path, pending_repo_factory
) -> None:
    # Found for real, not hypothetically: a manual smoke-test run left an arq
    # job queued from an earlier pytest run (real POST /repos enqueues a real
    # job); by the time an actual worker picked it up, clean_db had already
    # wiped its target row, and complete_snapshot's original bare `raise
    # ValueError` crashed the job with an ugly traceback instead of treating
    # a vanished target as "nothing to do" — same tolerance set_snapshot_status
    # already had. This is also a real production scenario (repo deleted
    # while an index is in flight), not just a test-hygiene artifact.
    git_url = git_fixture_repo.as_uri()
    repo_id, snapshot_id = await pending_repo_factory(SourceType.git_url, git_url)

    async with async_session_factory() as session:
        # repo.latest_snapshot_id FK must be cleared first — same ordering
        # clean_db (tests/conftest.py) uses, for the same reason.
        repo = await session.get(Repo, repo_id)
        assert repo is not None
        repo.latest_snapshot_id = None
        session.add(repo)

        snapshot = await session.get(AnalysisSnapshot, snapshot_id)
        assert snapshot is not None
        await session.delete(snapshot)
        await session.commit()

    # Must not raise.
    await index_repo(
        {"redis": redis_pool},
        snapshot_id=snapshot_id,
        repo_id=repo_id,
        source_type=SourceType.git_url.value,
        git_url=git_url,
    )
