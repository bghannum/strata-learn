"""Exercises the Phase 1.5 checkpoint from PROJECT_PLAN.md §12 directly: add a
repo, watch status transition pending -> parsing -> ready over the websocket.

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
from app.semantics.module_summarizer import ModuleSummaryOutput
from app.semantics.pattern_detector import PatternClaimOutput
from app.worker.pipeline import index_repo


def _fake_llm() -> FakeLLMProvider:
    # git_fixture_repo (tests/conftest.py) is a single file with no imports —
    # module_summarizer and pattern_detector each make one call;
    # identify_decision_points finds nothing, so extract_tradeoffs never calls
    # the LLM. A real ANTHROPIC_API_KEY isn't even loaded in this test process
    # (no backend/.env), so an omitted `llm` would fail loudly, not silently
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
            LLMResponse(
                text="",
                parsed=PatternClaimOutput(primary_pattern="modular monolith", confidence="medium", evidence=[], caveats=None),
                model="fake-model",
                stop_reason="end_turn",
                usage={},
            ),
        ]
    )


def _run_pipeline_in_background(*, snapshot_id: UUID, repo_id: UUID, git_url: str) -> threading.Thread:
    def target() -> None:
        async def _run() -> None:
            pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
            try:
                await index_repo(
                    {"redis": pool},
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
    repo_id, snapshot_id = asyncio.run(pending_repo_factory(SourceType.git_url, git_url))

    with TestClient(app) as client, client.websocket_connect(f"/repos/{repo_id}/progress") as ws:
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

    assert statuses == ["pending", "parsing", "analyzing", "ready"]
