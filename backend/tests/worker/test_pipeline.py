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

from arq.connections import ArqRedis

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
    assert await redis_pool.get(zip_redis_key) is None  # consumed + deleted, not left lingering


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
