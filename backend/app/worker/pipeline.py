"""The indexing pipeline, run as an arq job (Phase 1.5, D13/ADR-002). Moves the
Phase 1 synchronous logic (clone/extract -> analyze -> persist) into the worker
process, publishing AnalysisSnapshot.status transitions over Redis pub/sub as it
goes so `WS /repos/{repo_id}/progress` can relay them live.

Phase 2 (Layer B — semantic analysis) added the `analyzing` transition: after
Layer A (analyze_source/complete_snapshot) finishes, run_layer_b runs the
LLM-backed module summarizer, pattern detector, and trade-off extractor, then
moves the snapshot to `generating`. Phase 3 (study guide assembly) then runs
run_study_guide_generation, which assembles the StudyGuide/Section/Citation
rows from that Layer A/B data and moves the snapshot to `ready`.

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
from app.db.models import AnalysisSnapshot, SnapshotStatus, SourceType
from app.db.session import async_session_factory
from app.ingestion.source import (
    SourcePreparationError,
    cleanup_workspace,
    clone_git_repo,
    extract_zip_upload,
)
from app.generation.study_guide_builder import run_study_guide_generation
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
        # Short-circuit a redelivery of an already-fully-completed job (found
        # via Codex's Phase 2 pre-push review): arq is at-least-once, so a
        # worker crash between run_layer_b's own commit and arq acking the
        # job can redeliver it even though the snapshot is already `ready`.
        # Without this, the redelivered attempt would re-run the entire
        # pipeline from scratch, including Layer B's billed, non-deterministic
        # LLM calls — and if *that* redundant run then fails partway,
        # run_layer_b's own delete-then-insert idempotency (needed for the
        # normal, not-yet-complete redelivery case) would delete the already-
        # good rows before the redundant run's replacements are ready,
        # turning a fully successful snapshot into a failed one. If it's
        # already ready, there's nothing left to do.
        async with async_session_factory() as session:
            existing = await session.get(AnalysisSnapshot, snapshot_id)
        if existing is not None and existing.status == SnapshotStatus.ready:
            # A failure on just this notification must not fall through to
            # the except Exception below — the snapshot is already correctly
            # `ready`; only the publish attempt failed, and there's nothing
            # to compensate for. Letting it fall through would mark an
            # already-successful snapshot "failed", defeating the entire
            # point of this short-circuit (found via Codex's Phase 2
            # pre-push review — this exact block was the second thing it
            # flagged in the same diff).
            try:
                await publish(SnapshotStatus.ready)
            except Exception:  # noqa: BLE001, S110 — deliberately swallowed, see comment above
                pass
            return

        # Phase 3's counterpart to the `ready` short-circuit above: a
        # redelivery landing between run_layer_b's `generating` commit and
        # persist_study_guide's `ready` commit (a hard worker crash — a
        # graceful timeout/shutdown goes through the CancelledError handler
        # below instead, which already fails+cleans up terminally, so this
        # is specifically the "process died mid-flight" case) must not
        # re-run Layer A/B from scratch just to redo the one step after them.
        # Both are already persisted at this status (found via Codex's Phase
        # 3 pre-push review); only source needs re-acquiring — the crashed
        # attempt's temp workspace may or may not have survived, and citation
        # snippet capture needs real files on disk regardless — and study
        # guide generation needs to (re)run.
        resuming_from_generating = existing is not None and existing.status == SnapshotStatus.generating

        # Constructed inside the try, not before it, for the same reason as the
        # comment below: a missing/invalid ANTHROPIC_API_KEY (AnthropicProvider
        # raises ValueError on a falsy one) needs to reach the same fail_snapshot
        # + publish(failed) terminal-state guarantee as every other failure here,
        # not crash the job before "parsing" is even recorded.
        llm = llm or AnthropicProvider(api_key=settings.anthropic_api_key)  # type: ignore[arg-type]

        # arq runs concurrent jobs on one event loop, same as the API process
        # — clone_git_repo/extract_zip_upload are both blocking (subprocess +
        # disk I/O). Run them in a thread so a slow clone can't stall every
        # other queued job, progress publishing, and this worker's own
        # liveness along with it. (The API's own pre-check bounds
        # obviously-bad URLs before enqueueing, but a remote can still turn
        # slow *after* that passes — this is a separate, later window it
        # doesn't cover.) Shared by both the normal path and the
        # resume-from-`generating` path below.
        async def _acquire_source(pinned_commit: str | None = None):
            # Clear any workspace retained from a crashed earlier attempt
            # before reacquiring — cleanup_workspace normally runs in this
            # function's own `finally`, but a hard kill (not a graceful
            # CancelledError, which does reach `finally`) skips it entirely,
            # and cloning into or extracting over a non-empty leftover
            # directory either fails outright or silently mixes stale files
            # into the reacquired source (found via Codex's Phase 3 pre-push
            # review).
            cleanup_workspace(job_id)
            if source_type == SourceType.git_url.value:
                return await asyncio.to_thread(clone_git_repo, git_url, job_id, pinned_commit)  # type: ignore[arg-type]
            zip_bytes = await redis.get(zip_redis_key)
            if zip_bytes is None:
                raise SourcePreparationError("Uploaded zip is no longer available (expired or already consumed)")
            return await asyncio.to_thread(extract_zip_upload, io.BytesIO(zip_bytes), job_id), None

        if resuming_from_generating:
            snapshot = existing
            # Pinned to the commit Layer A/B data already describes — a fresh
            # tip clone could have moved on since the original analysis,
            # disagreeing with already-persisted line ranges and evidence
            # (found via Codex's Phase 3 pre-push review).
            source_dir, _commit_hash = await _acquire_source(existing.commit_hash)
        else:
            # Inside the try, not before it: if this specific publish is what
            # fails (Redis hiccup right here), the snapshot's already committed
            # "parsing" — leaving it unguarded would strand it there forever with
            # no failure ever recorded, same class of bug as an uncaught
            # exception later in the pipeline.
            async with async_session_factory() as session:
                await set_snapshot_status(session, snapshot_id, SnapshotStatus.parsing)
            await publish(SnapshotStatus.parsing)

            source_dir, commit_hash = await _acquire_source()
            analysis = await asyncio.to_thread(analyze_source, source_dir)

            # final_status=analyzing (not the default `ready`): Layer B runs next,
            # so this must be the only commit — otherwise `ready` briefly becomes
            # externally visible to a poller or a WS client connecting in the gap
            # before the follow-up transition below, and such a client disconnects
            # immediately on `ready`, never seeing Layer B run or fail (found via
            # Codex's Phase 2 pre-push review; see complete_snapshot's docstring).
            async with async_session_factory() as session:
                snapshot = await complete_snapshot(
                    session, snapshot_id, commit_hash, analysis, final_status=SnapshotStatus.analyzing
                )
            if snapshot is not None:
                await publish(SnapshotStatus.analyzing)

        # LAYER B — Phase 2, then study guide assembly — Phase 3. Still inside
        # the try: source_dir must still exist for both (the trade-off
        # extractor reads real code bodies from it since CodeUnit only stores
        # signatures, and citation.py captures every Citation's snippet_text
        # from it), so cleanup_workspace in the finally below must not run
        # until both finish. A vanished snapshot (see complete_snapshot's own
        # None-return case) skips both entirely — nothing left to attach them to.
        if snapshot is not None:
            if not resuming_from_generating:
                # run_layer_b sets `generating`, not `ready`, in the same commit
                # as the Layer B rows — see its comment. It manages its own
                # sessions internally (a short-lived read, then a short-lived
                # write only after all LLM calls finish) — no session passed in.
                await run_layer_b(llm, snapshot, source_dir)
                # Guarded the same way the "already ready" short-circuit's own
                # publish is guarded above: Layer B's billed rows are already
                # committed at this point, so a failure on just this
                # notification must not fall through to the generic except
                # Exception below, which would mark this snapshot `failed` and
                # make the next redelivery miss resuming_from_generating —
                # repeating every billed Layer B call for nothing (found via
                # Codex's Phase 3 pre-push review).
                try:
                    await publish(SnapshotStatus.generating)
                except Exception:  # noqa: BLE001, S110 — deliberately swallowed, see comment above
                    pass

            # run_study_guide_generation sets the final "ready" status itself,
            # in the same commit as the study guide rows — same invariant,
            # one step later. Also manages its own sessions internally.
            await run_study_guide_generation(llm, snapshot, source_dir)
    except SourcePreparationError as exc:
        async with async_session_factory() as session:
            await fail_snapshot(session, snapshot_id)
        await publish(SnapshotStatus.failed, error=str(exc))
        return
    except asyncio.CancelledError:
        # Found via the Phase 2 manual checkpoint: a job timeout (arq's
        # WorkerSettings.job_timeout) or worker shutdown cancels the running
        # task via asyncio.CancelledError, a BaseException — `except Exception`
        # below never catches it. Without any handling here, the snapshot got
        # stuck at "analyzing" forever with every WS client hanging, the exact
        # failure mode the Exception handler below already guards against for
        # every other kind of crash.
        #
        # Always mark it failed here — do NOT try to guess whether arq will
        # retry based on job_try/max_tries. A prior version of this handler
        # did exactly that and was wrong: verified empirically (a standalone
        # probe against a real arq Worker + Redis, not just reading the
        # source) that a job_timeout expiry is converted by
        # asyncio.wait_for/asyncio.timeout() into a plain TimeoutError before
        # arq's own retry check ever runs — TimeoutError isn't
        # asyncio.CancelledError, so arq's `retry_jobs` branch (which only
        # matches CancelledError/RetryJob) never fires, and the job
        # terminally fails on the very first attempt regardless of
        # max_tries. index_repo has no way to distinguish that (the common,
        # never-retried case) from a worker-shutdown-triggered CancelledError
        # (which arq genuinely might retry) — both look identical from in
        # here. Given that asymmetry, always marking failed is the safe
        # default: the cost of an occasional premature "failed" on a rare
        # shutdown-retry is far smaller than the cost of a permanently stuck
        # snapshot on the common timeout path.
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
