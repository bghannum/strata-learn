"""index_repo runs against real Postgres + Redis (no mocking — consistent with
this project's testing philosophy, see tests/conftest.py::clean_db). Exercises
the worker function directly rather than through a running arq worker process
— pytest doesn't have one, and testing the job function's own logic is the
actual unit of behavior that matters here.

Every call passes an explicit `llm=` — a real ANTHROPIC_API_KEY is present in
this project's .env, so an omitted `llm` would construct a real
AnthropicProvider and make live, billed API calls the moment index_repo
reaches Layer B.
"""

import asyncio
import io
import json
import subprocess
import zipfile
from collections import deque
from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import UUID

import pytest
from arq.connections import ArqRedis
from pydantic import BaseModel
from sqlmodel import select

import app.worker.pipeline as pipeline_module
from app.analysis.snapshot import analyze_source, complete_snapshot
from app.db.models import (
    AnalysisSnapshot,
    Citation,
    ModuleSummary,
    PatternClaim,
    Repo,
    Section,
    SectionType,
    SnapshotStatus,
    SourceType,
    StudyGuide,
    TradeoffCard,
)
from app.db.session import async_session_factory
from app.ingestion.source import JOBS_ROOT
from app.semantics.llm_provider import FakeLLMProvider, LLMResponse, Message
from app.semantics.module_summarizer import ModuleSummaryOutput
from app.semantics.orchestrator import run_layer_b
from app.semantics.pattern_detector import PatternClaimOutput, PatternEvidenceItem
from app.semantics.subsystem_namer import SubsystemNameOutput
from app.semantics.tradeoff_extractor import EvidenceRef, TradeoffCardOutput
from app.worker.pipeline import index_repo, progress_channel


def _module_summary_response() -> LLMResponse:
    return LLMResponse(
        text="",
        parsed=ModuleSummaryOutput(purpose="does a thing", role_in_system="a module", key_concepts=["concept"]),
        model="fake-model",
        stop_reason="end_turn",
        usage={"input_tokens": 1, "output_tokens": 1},
    )


def _subsystem_name_response() -> LLMResponse:
    # subsystem_namer runs between module summaries and pattern detection, and
    # makes exactly one call for the whole partition. The keys it returns don't
    # have to match: an unmatched partition falls back to a deterministic name
    # rather than disappearing, which is the behavior these pipeline tests
    # actually depend on.
    return LLMResponse(
        text="",
        parsed=SubsystemNameOutput(subsystems=[]),
        model="fake-model",
        stop_reason="end_turn",
        usage={"input_tokens": 1, "output_tokens": 1},
    )


def _pattern_claim_response(file_path: str = "app.py") -> LLMResponse:
    # A non-empty, resolvable evidence item — detect_pattern now drops the
    # whole claim if every evidence item is uncited (see pattern_detector.py).
    return LLMResponse(
        text="",
        parsed=PatternClaimOutput(
            primary_pattern="modular monolith",
            confidence="medium",
            evidence=[PatternEvidenceItem(claim="single-file repo", supporting_paths=[file_path])],
            caveats=None,
        ),
        model="fake-model",
        stop_reason="end_turn",
        usage={"input_tokens": 1, "output_tokens": 1},
    )


def _tradeoff_card_response(file_path: str = "worker.py", line_end: int = 5) -> LLMResponse:
    # At least one validated (non-seed) evidence_ref — extract_tradeoffs now
    # drops the whole card if the LLM provided none (see tradeoff_extractor.py).
    return LLMResponse(
        text="",
        parsed=TradeoffCardOutput(
            decision="use arq for background jobs",
            alternatives_considered=["direct synchronous call", "celery"],
            likely_reasoning="lighter weight, async-native",
            tradeoff_cost="extra infra (redis) to operate",
            confidence="medium",
            evidence_refs=[EvidenceRef(file_path=file_path, line_start=1, line_end=line_end)],
        ),
        model="fake-model",
        stop_reason="end_turn",
        usage={"input_tokens": 1, "output_tokens": 1},
    )


def _no_decision_point_llm(file_path: str = "app.py") -> FakeLLMProvider:
    """For a single-file fixture repo with no imports: module_summarizer,
    subsystem_namer, and pattern_detector each make one call;
    identify_decision_points finds nothing (no fan-in/out, no infra imports),
    so extract_tradeoffs never calls the LLM at all."""
    return FakeLLMProvider(
        [_module_summary_response(), _subsystem_name_response(), _pattern_claim_response(file_path)]
    )


class _BoomLLMProvider:
    async def complete(
        self, system: str, messages: list[Message], response_schema: type[BaseModel] | None = None
    ) -> LLMResponse:
        raise RuntimeError("simulated LLM crash")


class _CancelledLLMProvider:
    async def complete(
        self, system: str, messages: list[Message], response_schema: type[BaseModel] | None = None
    ) -> LLMResponse:
        raise asyncio.CancelledError


class _ProbeLLMProvider:
    """Records whether the job's temp workspace still exists on disk at the
    moment each complete() call is made — verifies cleanup_workspace doesn't
    run until after Layer B, since the trade-off extractor needs real source
    on disk (see pipeline.py's Layer B comment)."""

    def __init__(self, job_id: UUID, responses: list[LLMResponse]) -> None:
        self._job_id = job_id
        self._responses = deque(responses)
        self.workspace_existed_calls: list[bool] = []

    async def complete(
        self, system: str, messages: list[Message], response_schema: type[BaseModel] | None = None
    ) -> LLMResponse:
        self.workspace_existed_calls.append((JOBS_ROOT / str(self._job_id)).exists())
        return self._responses.popleft()


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
            llm=_no_decision_point_llm(),
        )

    messages = await _collect_published(redis_pool, progress_channel(snapshot_id), run)
    assert [m["status"] for m in messages] == [
        SnapshotStatus.parsing.value,
        SnapshotStatus.analyzing.value,
        SnapshotStatus.generating.value,
        SnapshotStatus.ready.value,
    ]

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
            llm=_no_decision_point_llm("hello.py"),
        )

    messages = await _collect_published(redis_pool, progress_channel(snapshot_id), run)
    assert [m["status"] for m in messages] == [
        SnapshotStatus.parsing.value,
        SnapshotStatus.analyzing.value,
        SnapshotStatus.generating.value,
        SnapshotStatus.ready.value,
    ]

    snapshot = await _get_snapshot(snapshot_id)
    assert snapshot.status == SnapshotStatus.ready
    assert snapshot.file_count == 1
    # Deliberately NOT deleted on success either — a worker crash between
    # this commit and arq recording the success can redeliver the job, and a
    # retry needs the source data too. Cleanup is the TTL set in api/repos.py
    # alone, on purpose (see worker/pipeline.py's module docstring).
    assert await redis_pool.get(zip_redis_key) == buf.getvalue()


async def test_index_repo_runs_layer_b_and_persists_all_three_tables(
    redis_pool: ArqRedis, pending_repo_factory
) -> None:
    # Unlike the plain success tests above, this file imports arq — a known
    # "infra" package — so identify_decision_points flags it, and
    # extract_tradeoffs makes a third LLM call. Exercises all three Layer B
    # tables getting rows, not just two of three.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("worker.py", "import arq\n\n\ndef main():\n    pass\n")

    repo_id, snapshot_id = await pending_repo_factory(SourceType.zip_upload, "upload.zip")
    zip_redis_key = f"zip-upload:{snapshot_id}"
    await redis_pool.set(zip_redis_key, buf.getvalue())

    llm = FakeLLMProvider(
        [
            _module_summary_response(),
            _subsystem_name_response(),
            _pattern_claim_response("worker.py"),
            _tradeoff_card_response("worker.py"),
        ]
    )

    await index_repo(
        {"redis": redis_pool},
        snapshot_id=snapshot_id,
        repo_id=repo_id,
        source_type=SourceType.zip_upload.value,
        zip_redis_key=zip_redis_key,
        llm=llm,
    )

    snapshot = await _get_snapshot(snapshot_id)
    assert snapshot.status == SnapshotStatus.ready

    async with async_session_factory() as session:
        summaries = list((await session.exec(select(ModuleSummary).where(ModuleSummary.snapshot_id == snapshot_id))).all())
        patterns = list((await session.exec(select(PatternClaim).where(PatternClaim.snapshot_id == snapshot_id))).all())
        cards = list((await session.exec(select(TradeoffCard).where(TradeoffCard.snapshot_id == snapshot_id))).all())
        guides = list((await session.exec(select(StudyGuide).where(StudyGuide.snapshot_id == snapshot_id))).all())

    assert len(summaries) == 1
    assert len(patterns) == 1
    assert len(cards) == 1
    assert cards[0].decision == "use arq for background jobs"
    # Phase 3: the pipeline doesn't stop at Layer B anymore — it assembles a
    # study guide from these same rows and that's what actually sets `ready`
    # (see study_guide_builder.persist_study_guide).
    assert len(guides) == 1


async def test_index_repo_layer_b_failure_marks_failed(
    redis_pool: ArqRedis, git_fixture_repo: Path, pending_repo_factory
) -> None:
    git_url = git_fixture_repo.as_uri()
    repo_id, snapshot_id = await pending_repo_factory(SourceType.git_url, git_url)

    async def run() -> None:
        with pytest.raises(RuntimeError, match="simulated LLM crash"):
            await index_repo(
                {"redis": redis_pool},
                snapshot_id=snapshot_id,
                repo_id=repo_id,
                source_type=SourceType.git_url.value,
                git_url=git_url,
                llm=_BoomLLMProvider(),
            )

    messages = await _collect_published(redis_pool, progress_channel(snapshot_id), run)
    assert [m["status"] for m in messages] == [
        SnapshotStatus.parsing.value,
        SnapshotStatus.analyzing.value,
        SnapshotStatus.failed.value,
    ]
    assert messages[-1]["error"] == "Indexing failed unexpectedly"

    snapshot = await _get_snapshot(snapshot_id)
    assert snapshot.status == SnapshotStatus.failed
    # cleanup_workspace still ran, even though the failure happened in Layer B
    assert not (JOBS_ROOT / str(snapshot_id)).exists()


async def test_index_repo_cancelled_during_layer_b_marks_failed(
    redis_pool: ArqRedis, git_fixture_repo: Path, pending_repo_factory
) -> None:
    # Found via the Phase 2 manual checkpoint: arq cancels a running job via
    # asyncio.CancelledError (job_timeout, worker shutdown) — a BaseException,
    # not an Exception, so `except Exception` alone left the snapshot stuck at
    # "analyzing" forever with no failure ever recorded. Always marks failed,
    # unconditionally — see pipeline.py's CancelledError handler for why a
    # job_try/max_tries-based "maybe arq will retry this" check is actually
    # wrong (confirmed via a direct empirical probe against a real arq
    # Worker: job_timeout expiry is never retried, regardless of max_tries).
    git_url = git_fixture_repo.as_uri()
    repo_id, snapshot_id = await pending_repo_factory(SourceType.git_url, git_url)

    async def run() -> None:
        with pytest.raises(asyncio.CancelledError):
            await index_repo(
                {"redis": redis_pool},
                snapshot_id=snapshot_id,
                repo_id=repo_id,
                source_type=SourceType.git_url.value,
                git_url=git_url,
                llm=_CancelledLLMProvider(),
            )

    messages = await _collect_published(redis_pool, progress_channel(snapshot_id), run)
    assert [m["status"] for m in messages] == [
        SnapshotStatus.parsing.value,
        SnapshotStatus.analyzing.value,
        SnapshotStatus.failed.value,
    ]
    assert messages[-1]["error"] == "Indexing was cancelled (timed out or worker shutdown)"

    snapshot = await _get_snapshot(snapshot_id)
    assert snapshot.status == SnapshotStatus.failed
    assert not (JOBS_ROOT / str(snapshot_id)).exists()


async def test_index_repo_layer_b_reads_source_before_cleanup(
    redis_pool: ArqRedis, git_fixture_repo: Path, pending_repo_factory
) -> None:
    # Verifies the cleanup-timing fix directly: cleanup_workspace must not run
    # until after Layer B's LLM calls, since extract_tradeoffs reads real code
    # bodies from the still-on-disk source directory.
    git_url = git_fixture_repo.as_uri()
    repo_id, snapshot_id = await pending_repo_factory(SourceType.git_url, git_url)

    probe = _ProbeLLMProvider(
        snapshot_id, [_module_summary_response(), _subsystem_name_response(), _pattern_claim_response()]
    )

    await index_repo(
        {"redis": redis_pool},
        snapshot_id=snapshot_id,
        repo_id=repo_id,
        source_type=SourceType.git_url.value,
        git_url=git_url,
        llm=probe,
    )

    assert probe.workspace_existed_calls  # at least one LLM call was made
    assert all(probe.workspace_existed_calls)
    # and cleanup did eventually happen, after Layer B finished
    assert not (JOBS_ROOT / str(snapshot_id)).exists()


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
            llm=FakeLLMProvider([]),
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
                llm=FakeLLMProvider([]),
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
            llm=FakeLLMProvider([]),
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

    # Must not raise. Layer B is skipped entirely (snapshot is None after
    # complete_snapshot), so the LLM is never called — an empty FakeLLMProvider
    # proves that.
    await index_repo(
        {"redis": redis_pool},
        snapshot_id=snapshot_id,
        repo_id=repo_id,
        source_type=SourceType.git_url.value,
        git_url=git_url,
        llm=FakeLLMProvider([]),
    )


async def test_index_repo_short_circuits_when_snapshot_already_ready(
    redis_pool: ArqRedis, layer_a_ready_factory
) -> None:
    # Found via Codex's Phase 2 pre-push review: at-least-once redelivery of
    # a job whose snapshot already reached "ready" must not re-run the whole
    # pipeline (Layer B is billed, non-deterministic LLM work) or risk
    # destroying already-good data via run_layer_b's own delete-then-insert
    # idempotency. _BoomLLMProvider would raise if Layer B were actually
    # invoked — proving it never is.
    repo_id, snapshot_id, _source_dir = await layer_a_ready_factory({"app.py": "x = 1\n"})

    async def run() -> None:
        await index_repo(
            {"redis": redis_pool},
            snapshot_id=snapshot_id,
            repo_id=repo_id,
            source_type=SourceType.git_url.value,
            git_url="file:///should-never-be-cloned",
            llm=_BoomLLMProvider(),
        )

    messages = await _collect_published(redis_pool, progress_channel(snapshot_id), run)
    assert [m["status"] for m in messages] == [SnapshotStatus.ready.value]

    snapshot = await _get_snapshot(snapshot_id)
    assert snapshot.status == SnapshotStatus.ready


async def test_index_repo_resumes_from_generating_without_rerunning_layer_b(
    redis_pool: ArqRedis, git_fixture_repo: Path, pending_repo_factory
) -> None:
    # Phase 3's counterpart to the "already ready" short-circuit above: a
    # redelivery landing between run_layer_b's `generating` commit and
    # persist_study_guide's `ready` commit (a hard worker crash — a graceful
    # timeout/shutdown goes through the CancelledError path instead, which
    # already fails+cleans up terminally) must not re-run Layer A/B a second
    # time. Build that exact state by hand (Layer A + Layer B already done,
    # status=generating) rather than via index_repo itself, then redeliver
    # with a boom LLM to prove Layer B is skipped, not just idempotent.
    git_url = git_fixture_repo.as_uri()
    repo_id, snapshot_id = await pending_repo_factory(SourceType.git_url, git_url)

    analysis = analyze_source(git_fixture_repo)
    async with async_session_factory() as session:
        snapshot = await complete_snapshot(
            session, snapshot_id, None, analysis, final_status=SnapshotStatus.analyzing
        )
        assert snapshot is not None
    await run_layer_b(_no_decision_point_llm(), snapshot, git_fixture_repo)

    async with async_session_factory() as session:
        pre = await session.get(AnalysisSnapshot, snapshot_id)
        assert pre is not None
        assert pre.status == SnapshotStatus.generating
        pre_summaries = list(
            (await session.exec(select(ModuleSummary).where(ModuleSummary.snapshot_id == snapshot_id))).all()
        )
    assert len(pre_summaries) == 1  # sanity: Layer B actually ran and persisted

    async def run() -> None:
        await index_repo(
            {"redis": redis_pool},
            snapshot_id=snapshot_id,
            repo_id=repo_id,
            source_type=SourceType.git_url.value,
            git_url=git_url,
            # Would raise if Layer B (or diagram_builder's label call) ran
            # again — proving the resume path skips both, not just tolerates
            # re-running them via existing delete-then-insert idempotency.
            llm=_BoomLLMProvider(),
        )

    messages = await _collect_published(redis_pool, progress_channel(snapshot_id), run)
    assert [m["status"] for m in messages] == [SnapshotStatus.ready.value]

    snapshot = await _get_snapshot(snapshot_id)
    assert snapshot.status == SnapshotStatus.ready

    async with async_session_factory() as session:
        post_summaries = list(
            (await session.exec(select(ModuleSummary).where(ModuleSummary.snapshot_id == snapshot_id))).all()
        )
        guides = list((await session.exec(select(StudyGuide).where(StudyGuide.snapshot_id == snapshot_id))).all())
    assert len(post_summaries) == 1  # unchanged — not re-run, not duplicated
    assert len(guides) == 1


def _commit_repo_file(repo_dir: Path, content: str, message: str) -> str:
    (repo_dir / "app.py").write_text(content)
    subprocess.run(["git", "add", "app.py"], cwd=repo_dir, check=True)
    subprocess.run(
        ["git", "-c", "user.email=test@test.com", "-c", "user.name=test", "commit", "-q", "-m", message],
        cwd=repo_dir,
        check=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_dir, check=True, capture_output=True, text=True
    ).stdout.strip()


async def test_index_repo_resume_pins_commit_and_clears_stale_workspace(
    redis_pool: ArqRedis, tmp_path: Path, pending_repo_factory
) -> None:
    # Two real bugs found alongside the resume path above (Codex's Phase 3
    # pre-push review): (1) a leftover workspace from the crashed original
    # attempt must not break re-cloning, and (2) the remote branch may have
    # moved on since the original analysis — resume must reacquire the exact
    # analyzed commit, not the new tip, since persisted Layer A/B data still
    # describes the old one.
    repo_dir = tmp_path / "multi-commit-repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_dir, check=True)
    v1_sha = _commit_repo_file(repo_dir, "print('v1')\n", "v1")

    git_url = repo_dir.as_uri()
    repo_id, snapshot_id = await pending_repo_factory(SourceType.git_url, git_url)

    analysis = analyze_source(repo_dir)
    async with async_session_factory() as session:
        snapshot = await complete_snapshot(
            session, snapshot_id, v1_sha, analysis, final_status=SnapshotStatus.analyzing
        )
        assert snapshot is not None
    await run_layer_b(_no_decision_point_llm(), snapshot, repo_dir)

    # Simulate a leftover workspace from the crashed original attempt: a
    # stale file sitting exactly where clone_git_repo would try to clone.
    stale_dir = JOBS_ROOT / str(snapshot_id) / "source"
    stale_dir.mkdir(parents=True, exist_ok=True)
    (stale_dir / "stale.txt").write_text("leftover from a crashed attempt\n")

    # The remote moves on after the original analysis.
    _commit_repo_file(repo_dir, "print('v2')\n", "v2")

    async def run() -> None:
        await index_repo(
            {"redis": redis_pool},
            snapshot_id=snapshot_id,
            repo_id=repo_id,
            source_type=SourceType.git_url.value,
            git_url=git_url,
            llm=_BoomLLMProvider(),
        )

    messages = await _collect_published(redis_pool, progress_channel(snapshot_id), run)
    assert [m["status"] for m in messages] == [SnapshotStatus.ready.value]

    snapshot = await _get_snapshot(snapshot_id)
    assert snapshot.status == SnapshotStatus.ready
    assert snapshot.commit_hash == v1_sha  # unchanged — resume didn't silently drift to v2

    async with async_session_factory() as session:
        guide = (await session.exec(select(StudyGuide).where(StudyGuide.snapshot_id == snapshot_id))).one()
        sections = list((await session.exec(select(Section).where(Section.study_guide_id == guide.id))).all())
        deep_dive = next(s for s in sections if s.section_type == SectionType.deep_dive)
        citations = list((await session.exec(select(Citation).where(Citation.section_id == deep_dive.id))).all())

    app_citation = next(c for c in citations if c.file_path == "app.py")
    assert "v1" in app_citation.snippet_text
    assert "v2" not in app_citation.snippet_text


async def test_index_repo_generating_publish_failure_does_not_mark_failed(
    redis_pool: ArqRedis, git_fixture_repo: Path, pending_repo_factory, monkeypatch
) -> None:
    # Mirrors the "ready" short-circuit publish-failure test below: a
    # failure on just the `generating` notification — right after
    # run_layer_b commits its billed rows — must not fall through to the
    # generic exception handler and mark the snapshot failed. That would
    # make the next redelivery miss resuming_from_generating and repeat
    # every billed Layer B call for nothing (found via Codex's Phase 3
    # pre-push review).
    git_url = git_fixture_repo.as_uri()
    repo_id, snapshot_id = await pending_repo_factory(SourceType.git_url, git_url)

    real_publish = redis_pool.publish

    async def _flaky_publish(channel, data):
        if '"generating"' in data:
            raise RuntimeError("simulated Redis publish failure")
        return await real_publish(channel, data)

    monkeypatch.setattr(redis_pool, "publish", _flaky_publish)

    # Must not raise, and must still reach `ready` despite the dropped notification.
    await index_repo(
        {"redis": redis_pool},
        snapshot_id=snapshot_id,
        repo_id=repo_id,
        source_type=SourceType.git_url.value,
        git_url=git_url,
        llm=_no_decision_point_llm(),
    )

    snapshot = await _get_snapshot(snapshot_id)
    assert snapshot.status == SnapshotStatus.ready


async def test_index_repo_short_circuit_publish_failure_does_not_mark_failed(
    redis_pool: ArqRedis, layer_a_ready_factory, monkeypatch
) -> None:
    # Found via Codex's Phase 2 pre-push review: a failure on just the
    # redundant "ready" re-publish above must not fall through to the
    # generic exception handler and flip an already-successful snapshot to
    # "failed" — the snapshot itself is already correct; only the
    # notification attempt failed, and there's nothing to compensate for.
    repo_id, snapshot_id, _source_dir = await layer_a_ready_factory({"app.py": "x = 1\n"})

    async def _boom_publish(*args, **kwargs):
        raise RuntimeError("simulated Redis publish failure")

    monkeypatch.setattr(redis_pool, "publish", _boom_publish)

    # Must not raise.
    await index_repo(
        {"redis": redis_pool},
        snapshot_id=snapshot_id,
        repo_id=repo_id,
        source_type=SourceType.git_url.value,
        git_url="file:///should-never-be-cloned",
        llm=_BoomLLMProvider(),
    )

    snapshot = await _get_snapshot(snapshot_id)
    assert snapshot.status == SnapshotStatus.ready
