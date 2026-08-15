"""Exercises the Phase 1.5 checkpoint from docs/design/original-project-plan.md §12 directly: add a
repo, watch status transition pending -> parsing -> analyzing -> generating ->
ready over the websocket.

No arq worker process runs during the test suite, so the pipeline is driven in
a background thread with its own event loop + Redis pool — this is what a real
worker process does when it picks a job off the queue, just invoked directly
instead of waiting for one to exist. Uses pending_repo_factory (tests/conftest.py)
rather than the real POST /repos endpoint so this test doesn't also leave a
real, never-consumed arq job sitting in the queue.
"""

import asyncio
import threading
from pathlib import Path
from uuid import UUID

from arq import create_pool
from arq.connections import RedisSettings
from fastapi.testclient import TestClient

from app.config import settings
from app.db.models import SourceType
from app.main import app
from app.semantics.llm_provider import FakeLLMProvider, LLMResponse
from app.generation.architecture_narrative import ArchitectureNarrativeOutput
from app.semantics.module_summarizer import ModuleSummaryOutput
from app.semantics.pattern_detector import PatternClaimOutput
from app.semantics.subsystem_namer import SubsystemNameOutput
from app.worker.pipeline import index_repo
from tests.conftest import register_test_user


def _fake_llm() -> FakeLLMProvider:
    # git_fixture_repo (tests/conftest.py) is a single file with no imports —
    # module_summarizer and pattern_detector each make one call;
    # identify_decision_points finds nothing, so extract_tradeoffs never calls
    # the LLM, and diagram_builder's node selection requires at least one
    # internal file-to-file edge, so it never calls the LLM either.
    # architecture_narrative does call it, once, during study-guide assembly —
    # a PatternClaim exists, which is enough to synthesize from. A real
    # ANTHROPIC_API_KEY isn't even loaded in this test process (no
    # backend/.env), so an omitted `llm` would fail loudly, not silently
    # bill — but a fake keeps this test decoupled from that either way.
    return FakeLLMProvider(
        [
            LLMResponse(
                text="",
                parsed=ModuleSummaryOutput(purpose="p", role_in_system="r", key_concepts=["c"]),
                model="fake-model",
                stop_reason="end_turn",
                usage={},
            ),
            # subsystem_namer's one call, between module summaries and pattern
            # detection
            LLMResponse(
                text="",
                parsed=SubsystemNameOutput(subsystems=[]),
                model="fake-model",
                stop_reason="end_turn",
                usage={},
            ),
            LLMResponse(
                text="",
                parsed=PatternClaimOutput(primary_pattern="modular monolith", confidence="medium", evidence=[], caveats=None),
                model="fake-model",
                stop_reason="end_turn",
                usage={},
            ),
            # architecture_narrative, during study-guide assembly
            LLMResponse(
                text="",
                parsed=ArchitectureNarrativeOutput(overview="A single-file app.", why_sections=[]),
                model="fake-model",
                stop_reason="end_turn",
                usage={},
            ),
        ]
    )


class _SilentPublishPool:
    """Wraps the real arq pool but drops every publish, standing in for a Redis
    that fails on exactly the terminal notification (#9). Everything else the
    pipeline does with Redis still goes through."""

    def __init__(self, pool) -> None:
        self._pool = pool

    async def publish(self, *args, **kwargs) -> int:
        return 0  # pretend it went nowhere — which, for pub/sub, it may not have

    def __getattr__(self, name):
        return getattr(self._pool, name)


def _run_pipeline_in_background(
    *, snapshot_id: UUID, repo_id: UUID, git_url: str, drop_publishes: bool = False
) -> threading.Thread:
    def target() -> None:
        async def _run() -> None:
            pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
            try:
                await index_repo(
                    {"redis": _SilentPublishPool(pool) if drop_publishes else pool},
                    snapshot_id=snapshot_id,
                    repo_id=repo_id,
                    source_type=SourceType.git_url.value,
                    git_url=git_url,
                    llm=_fake_llm(),
                )
            finally:
                await pool.aclose()

        asyncio.run(_run())

    thread = threading.Thread(target=target)
    thread.start()
    return thread


def test_progress_ws_reports_pending_parsing_ready(git_fixture_repo: Path, pending_repo_factory) -> None:
    # Sync test, not async — TestClient manages its own event loop/thread
    # internally, and nesting that inside an already-running async test frame
    # (pytest-asyncio's auto mode) risks loop-reentrancy issues. asyncio.run()
    # here gets its own throwaway loop, same pattern the background thread
    # below uses — safe under NullPool (db/session.py), which was chosen
    # specifically to tolerate exactly this kind of cross-loop access.
    git_url = git_fixture_repo.as_uri()

    with TestClient(app) as client:
        user = register_test_user(client)
        repo_id, snapshot_id = asyncio.run(
            pending_repo_factory(SourceType.git_url, git_url, user_id=UUID(user["id"]))
        )

        with client.websocket_connect(f"/repos/{repo_id}/progress") as ws:
            # First message is the WS handler's own DB read, sent right after it
            # subscribes to pub/sub — confirms it's actually listening before the
            # pipeline (which publishes over that same channel) starts. Starting
            # the pipeline any earlier risks it finishing before the WS subscribes,
            # which would silently drop the "pending"/"parsing" transitions.
            first = ws.receive_json()
            assert first["status"] == "pending"
            statuses = [first["status"]]

            thread = _run_pipeline_in_background(snapshot_id=snapshot_id, repo_id=repo_id, git_url=git_url)
            try:
                while True:
                    message = ws.receive_json()
                    statuses.append(message["status"])
                    if message["status"] in ("ready", "failed"):
                        break
            finally:
                thread.join(timeout=10)

    assert statuses == ["pending", "parsing", "analyzing", "generating", "ready"]


def test_progress_ws_falls_back_to_the_database_when_the_terminal_publish_is_lost(
    git_fixture_repo: Path, pending_repo_factory
) -> None:
    """#9: pub/sub is fire-and-forget, so a Redis failure on the worker's one
    terminal publish left a client that had already passed the initial DB check
    waiting forever — even though the snapshot was genuinely done.

    Simulated by never publishing at all: the pipeline runs with a publish that
    silently drops every message, so the *only* way this client can learn the
    job finished is the polling fallback re-reading the row.
    """
    git_url = git_fixture_repo.as_uri()

    with TestClient(app) as client:
        user = register_test_user(client)
        repo_id, snapshot_id = asyncio.run(
            pending_repo_factory(SourceType.git_url, git_url, user_id=UUID(user["id"]))
        )

        with client.websocket_connect(f"/repos/{repo_id}/progress") as ws:
            assert ws.receive_json()["status"] == "pending"

            thread = _run_pipeline_in_background(
                snapshot_id=snapshot_id, repo_id=repo_id, git_url=git_url, drop_publishes=True
            )
            try:
                # No message will ever arrive over pub/sub; this only returns
                # because the handler re-reads the snapshot on its poll timeout.
                message = ws.receive_json()
            finally:
                thread.join(timeout=30)

    assert message["status"] == "ready"
