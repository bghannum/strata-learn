"""The indexing pipeline, run as an arq job (Phase 1.5, D13/ADR-002). Moves the
Phase 1 synchronous logic (clone/extract -> analyze -> persist) into the worker
process, publishing AnalysisSnapshot.status transitions over Redis pub/sub as it
goes so `WS /repos/{repo_id}/progress` can relay them live.

Phase 2 (Layer B — semantic analysis) added the `analyzing` transition: after
Layer A (analyze_source/complete_snapshot) finishes, run_layer_b runs the
LLM-backed module summarizer, pattern detector, and trade-off extractor, then
the snapshot moves to `ready`. `generating` (Phase 3, study guide assembly)
stays defined-but-unused until that phase lands.

git_url jobs clone independently in this process — no dependency on anything the
API process touched. zip_upload jobs read the uploaded bytes back out of Redis
(stored by the API under `zip_redis_key`, see api/repos.py) since the worker runs
in a separate container/process with no shared filesystem with the API.

Deliberately never explicitly deletes `zip_redis_key` here, on any path — arq
is at-least-once, not exactly-once, and a worker crash between finishing
successfully and arq recording that success can redeliver the job; an eager
delete anywhere in this function risks a retry finding no source data left.
The TTL set on the key when it's written (api/repos.py) is the sole cleanup
mechanism, on purpose: simpler than tracking "is this outcome truly final"
per exit path, and correct under redelivery either way.
"""

import asyncio
import io
import json
from uuid import UUID

from app.analysis.snapshot import (
    analyze_source,
    complete_snapshot,
    fail_snapshot,
    set_snapshot_status,
)
from app.config import settings
from app.db.models import SnapshotStatus, SourceType
from app.db.session import async_session_factory
from app.ingestion.source import (
    SourcePreparationError,
    cleanup_workspace,
    clone_git_repo,
    extract_zip_upload,
)
from app.semantics.llm_provider import AnthropicProvider, LLMProvider
from app.semantics.orchestrator import run_layer_b


def progress_channel(snapshot_id: UUID) -> str:
    return f"snapshot-progress:{snapshot_id}"


async def index_repo(
    ctx: dict,
    snapshot_id: UUID,
    repo_id: UUID,
    source_type: str,
    git_url: str | None = None,
    zip_redis_key: str | None = None,
    llm: LLMProvider | None = None,
) -> None:
    redis = ctx["redis"]  # arq provides this per-job, same pool the worker itself uses
    channel = progress_channel(snapshot_id)

    async def publish(status: SnapshotStatus, error: str | None = None) -> None:
        payload: dict = {"status": status.value}
        if error is not None:
            payload["error"] = error
        await redis.publish(channel, json.dumps(payload))

    # job_id scopes the temp workspace (ingestion/source.py) — snapshot_id is
    # already a unique per-job identifier, no need to mint a separate one.
    job_id = snapshot_id
    try:
        # Constructed inside the try, not before it, for the same reason as the
        # comment below: a missing/invalid ANTHROPIC_API_KEY (AnthropicProvider
        # raises ValueError on a falsy one) needs to reach the same fail_snapshot
        # + publish(failed) terminal-state guarantee as every other failure here,
        # not crash the job before "parsing" is even recorded.
        llm = llm or AnthropicProvider(api_key=settings.anthropic_api_key)  # type: ignore[arg-type]

        # Inside the try, not before it: if this specific publish is what
        # fails (Redis hiccup right here), the snapshot's already committed
        # "parsing" — leaving it unguarded would strand it there forever with
        # no failure ever recorded, same class of bug as an uncaught
        # exception later in the pipeline.
        async with async_session_factory() as session:
            await set_snapshot_status(session, snapshot_id, SnapshotStatus.parsing)
        await publish(SnapshotStatus.parsing)

        # arq runs concurrent jobs on one event loop, same as the API process
        # — clone_git_repo/extract_zip_upload/analyze_source are all blocking
        # (subprocess + disk I/O + CPU-bound parsing). Run them in a thread so
        # a slow clone or a big repo's parse can't stall every other queued
        # job, progress publishing, and this worker's own liveness along with
        # it. (The API's own pre-check bounds obviously-bad URLs before
        # enqueueing, but a remote can still turn slow *after* that passes —
        # this is a separate, later window it doesn't cover.)
        if source_type == SourceType.git_url.value:
            source_dir, commit_hash = await asyncio.to_thread(clone_git_repo, git_url, job_id)  # type: ignore[arg-type]
        else:
            zip_bytes = await redis.get(zip_redis_key)
            if zip_bytes is None:
                raise SourcePreparationError("Uploaded zip is no longer available (expired or already consumed)")
            source_dir = await asyncio.to_thread(extract_zip_upload, io.BytesIO(zip_bytes), job_id)
            commit_hash = None

        analysis = await asyncio.to_thread(analyze_source, source_dir)

        async with async_session_factory() as session:
            snapshot = await complete_snapshot(session, snapshot_id, commit_hash, analysis)

        # LAYER B — Phase 2. Still inside the try: source_dir must still exist
        # (the trade-off extractor reads real code bodies from it, since
        # CodeUnit only stores signatures), so cleanup_workspace in the
        # finally below must not run until this finishes either way. A
        # vanished snapshot (see complete_snapshot's own None-return case)
        # skips Layer B entirely — nothing left to attach it to.
        if snapshot is not None:
            async with async_session_factory() as session:
                await set_snapshot_status(session, snapshot_id, SnapshotStatus.analyzing)
            await publish(SnapshotStatus.analyzing)

            async with async_session_factory() as session:
                await run_layer_b(session, llm, snapshot, source_dir)

            async with async_session_factory() as session:
                await set_snapshot_status(session, snapshot_id, SnapshotStatus.ready)
    except SourcePreparationError as exc:
        async with async_session_factory() as session:
            await fail_snapshot(session, snapshot_id)
        await publish(SnapshotStatus.failed, error=str(exc))
        return
    except asyncio.CancelledError:
        # Found via the Phase 2 manual checkpoint: a job timeout (arq's
        # WorkerSettings.job_timeout) or worker shutdown cancels the running
        # task via asyncio.CancelledError, a BaseException — `except Exception`
        # below never catches it. Without this, the snapshot got stuck at
        # "analyzing" forever with every WS client hanging, the exact failure
        # mode the Exception handler below already guards against for every
        # other kind of crash. Re-raise so arq's own cancellation/redelivery
        # handling still runs — complete_snapshot/run_layer_b are both
        # idempotent under redelivery already.
        async with async_session_factory() as session:
            await fail_snapshot(session, snapshot_id)
        await publish(SnapshotStatus.failed, error="Indexing was cancelled (timed out or worker shutdown)")
        raise
    except Exception:
        # Anything beyond a validated bad-source error (a parse crash, an
        # unexpected DB failure, ...) still needs to reach a terminal state —
        # otherwise the snapshot sits at "parsing" forever and every WS
        # client watching it hangs indefinitely. Mark failed, notify, then
        # re-raise so arq still sees/logs it.
        async with async_session_factory() as session:
            await fail_snapshot(session, snapshot_id)
        await publish(SnapshotStatus.failed, error="Indexing failed unexpectedly")
        raise
    finally:
        cleanup_workspace(job_id)

    if snapshot is None:
        return  # target vanished mid-job (see complete_snapshot) — no one left to notify
    await publish(SnapshotStatus.ready)
