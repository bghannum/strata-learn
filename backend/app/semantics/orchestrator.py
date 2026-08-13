"""Layer B persistence orchestrator. Mirrors the analyze_source (pure) /
complete_snapshot (persist) split in app/analysis/snapshot.py — module
summarizer, pattern detector, and trade-off extractor all stay pure/testable,
this module owns reading CodeUnit rows and writing the three new tables.

Runs the three passes sequentially (no concurrency in Phase 2, matching the
project's "boring, debuggable" bias — revisit if the Phase 2 checkpoint shows
indexing real repos is too slow).
"""

from pathlib import Path

from sqlmodel import delete, select

from app.db.models import (
    AnalysisSnapshot,
    CodeUnit,
    Confidence,
    ModuleSummary,
    PatternClaim,
    SnapshotStatus,
    TradeoffCard,
)
from app.db.session import async_session_factory
from app.semantics.chunking import chunk_by_module
from app.semantics.llm_provider import LLMProvider
from app.semantics.module_summarizer import summarize_modules
from app.semantics.pattern_detector import detect_pattern
from app.semantics.tradeoff_extractor import extract_tradeoffs, identify_decision_points


async def run_layer_b(llm: LLMProvider, snapshot: AnalysisSnapshot, source_dir: Path) -> None:
    # Short-lived read, closed before any LLM call runs (found via Codex's
    # Phase 2 pre-push review): the first SELECT in a session auto-begins a
    # Postgres transaction that stays open until commit/rollback. Holding it
    # open across up to MAX_CHUNKS_PER_SNAPSHOT sequential external LLM calls
    # — which can run for minutes — ties up a real connection the whole time
    # (this project uses NullPool, so every checkout is a real Postgres
    # connection, not a pooled one), which can starve concurrent indexing
    # jobs of connections and blocks autovacuum on the held transaction's
    # snapshot. No DB session is open at all during the LLM work below.
    async with async_session_factory() as session:
        result = await session.exec(select(CodeUnit).where(CodeUnit.snapshot_id == snapshot.id))
        code_units = list(result.all())

    chunks = chunk_by_module(code_units)
    summaries = await summarize_modules(llm, chunks, snapshot.dependency_graph)
    pattern = await detect_pattern(llm, snapshot.dependency_graph, code_units, snapshot.entry_points)
    decision_points = identify_decision_points(snapshot.dependency_graph, code_units, snapshot.entry_points)
    tradeoffs = await extract_tradeoffs(llm, decision_points, source_dir, snapshot.dependency_graph, code_units)

    # Fresh, short-lived write transaction — opened only now that every LLM
    # call has already finished, not held open across them.
    async with async_session_factory() as session:
        # arq is at-least-once, not exactly-once — delete existing rows for
        # this snapshot before inserting, mirroring complete_snapshot's
        # CodeUnit handling (app/analysis/snapshot.py) exactly, for the same
        # reason.
        await session.exec(delete(ModuleSummary).where(ModuleSummary.snapshot_id == snapshot.id))
        await session.exec(delete(PatternClaim).where(PatternClaim.snapshot_id == snapshot.id))
        await session.exec(delete(TradeoffCard).where(TradeoffCard.snapshot_id == snapshot.id))

        for summary in summaries:
            session.add(
                ModuleSummary(
                    snapshot_id=snapshot.id,
                    file_path=summary.file_path,
                    purpose=summary.purpose,
                    role_in_system=summary.role_in_system,
                    key_concepts=summary.key_concepts,
                    line_start=summary.line_start,
                    line_end=summary.line_end,
                    prompt_version=summary.prompt_version,
                    model=summary.model,
                )
            )

        if pattern is not None:
            # None means every evidence item lost its citations — see
            # pattern_detector.py. No claim persisted this run is better than
            # an uncited one (found via Codex's Phase 2 pre-push review).
            session.add(
                PatternClaim(
                    snapshot_id=snapshot.id,
                    primary_pattern=pattern.primary_pattern,
                    confidence=Confidence(pattern.confidence),
                    evidence=pattern.evidence,
                    caveats=pattern.caveats,
                    prompt_version=pattern.prompt_version,
                    model=pattern.model,
                )
            )

        for card in tradeoffs:
            session.add(
                TradeoffCard(
                    snapshot_id=snapshot.id,
                    decision=card.decision,
                    alternatives_considered=card.alternatives_considered,
                    likely_reasoning=card.likely_reasoning,
                    tradeoff_cost=card.tradeoff_cost,
                    confidence=Confidence(card.confidence),
                    evidence_refs=card.evidence_refs,
                    prompt_version=card.prompt_version,
                    model=card.model,
                )
            )

        # Set `generating` — not `ready` — in this same commit, not a later
        # separate one — found via Codex's Phase 2 pre-push review: a worker
        # crash between a separate status commit and this one left a window
        # where Layer B's data was fully persisted but the snapshot still
        # read "analyzing", so a redelivery in that window bypassed
        # index_repo's "already ready" short-circuit and repeated every
        # billed LLM call for nothing. snapshot was loaded in an earlier,
        # different session (pipeline.py's complete_snapshot call) — re-fetch
        # it in *this* session rather than mutating the detached object; a
        # vanished target (repo deleted mid-job) is tolerated the same way
        # set_snapshot_status already tolerates it.
        #
        # Phase 3: `ready` is now set by study_guide_builder.persist_study_guide
        # instead, once the study guide built from this data is itself
        # persisted — the same "final status commits with the data it
        # describes" rule, just one step later in the pipeline.
        current = await session.get(AnalysisSnapshot, snapshot.id)
        if current is not None:
            current.status = SnapshotStatus.generating
            session.add(current)

        await session.commit()
