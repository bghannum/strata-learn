"""The indexing pipeline, run as an arq job (Phase 1.5, D13/ADR-002). Moves the
Phase 1 synchronous logic (clone/extract -> analyze -> persist) into the worker
process, publishing AnalysisSnapshot.status transitions over Redis pub/sub as it
goes so `WS /repos/{repo_id}/progress` can relay them live.

Only pending -> parsing -> ready|failed are real this phase — analyzing/generating
stay defined-but-unused until Layer B (Phase 2) and generation (Phase 3) land real
work on top of this.

git_url jobs clone independently in this process — no dependency on anything the
API process touched. zip_upload jobs read the uploaded bytes back out of Redis
(stored by the API under `zip_redis_key`, see api/repos.py) since the worker runs
in a separate container/process with no shared filesystem with the API.
"""

import io
import json
from uuid import UUID

from app.analysis.snapshot import (
    analyze_source,
    complete_snapshot,
    fail_snapshot,
    set_snapshot_status,
)
from app.db.models import SnapshotStatus, SourceType
from app.db.session import async_session_factory
from app.ingestion.source import (
    SourcePreparationError,
    cleanup_workspace,
    clone_git_repo,
    extract_zip_upload,
)


def progress_channel(snapshot_id: UUID) -> str:
    return f"snapshot-progress:{snapshot_id}"


async def index_repo(
    ctx: dict,
    snapshot_id: UUID,
    repo_id: UUID,
    source_type: str,
    git_url: str | None = None,
    zip_redis_key: str | None = None,
) -> None:
    redis = ctx["redis"]  # arq provides this per-job, same pool the worker itself uses
    channel = progress_channel(snapshot_id)

    async def publish(status: SnapshotStatus, error: str | None = None) -> None:
        payload: dict = {"status": status.value}
        if error is not None:
            payload["error"] = error
        await redis.publish(channel, json.dumps(payload))

    async with async_session_factory() as session:
        await set_snapshot_status(session, snapshot_id, SnapshotStatus.parsing)
    await publish(SnapshotStatus.parsing)

    # job_id scopes the temp workspace (ingestion/source.py) — snapshot_id is
    # already a unique per-job identifier, no need to mint a separate one.
    job_id = snapshot_id
    try:
        if source_type == SourceType.git_url.value:
            source_dir, commit_hash = clone_git_repo(git_url, job_id)  # type: ignore[arg-type]
        else:
            zip_bytes = await redis.get(zip_redis_key)
            if zip_bytes is None:
                raise SourcePreparationError("Uploaded zip is no longer available (expired or already consumed)")
            source_dir = extract_zip_upload(io.BytesIO(zip_bytes), job_id)
            commit_hash = None

        analysis = analyze_source(source_dir)

        async with async_session_factory() as session:
            snapshot = await complete_snapshot(session, snapshot_id, commit_hash, analysis)
    except SourcePreparationError as exc:
        async with async_session_factory() as session:
            await fail_snapshot(session, snapshot_id)
        await publish(SnapshotStatus.failed, error=str(exc))
        return
    except Exception:
        # Anything beyond a validated bad-source error (a parse crash, an
        # unexpected DB failure, ...) still needs to reach a terminal state —
        # otherwise the snapshot sits at "parsing" forever and every WS
        # client watching it hangs indefinitely. Mark failed, notify, then
        # re-raise so arq still sees/logs/retries the underlying failure —
        # this doesn't replace that, it just stops it from being silent to
        # everyone downstream of the job itself.
        async with async_session_factory() as session:
            await fail_snapshot(session, snapshot_id)
        await publish(SnapshotStatus.failed, error="Indexing failed unexpectedly")
        raise
    finally:
        cleanup_workspace(job_id)
        if zip_redis_key:
            await redis.delete(zip_redis_key)

    if snapshot is None:
        return  # target vanished mid-job (see complete_snapshot) — no one left to notify
    await publish(SnapshotStatus.ready)
