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
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import (
    AnalysisSnapshot,
    CodeUnit,
    Confidence,
    ModuleSummary,
    PatternClaim,
    TradeoffCard,
)
from app.semantics.chunking import chunk_by_module
from app.semantics.llm_provider import LLMProvider
from app.semantics.module_summarizer import summarize_modules
from app.semantics.pattern_detector import detect_pattern
from app.semantics.tradeoff_extractor import extract_tradeoffs, identify_decision_points


async def run_layer_b(session: AsyncSession, llm: LLMProvider, snapshot: AnalysisSnapshot, source_dir: Path) -> None:
    result = await session.exec(select(CodeUnit).where(CodeUnit.snapshot_id == snapshot.id))
    code_units = list(result.all())

    chunks = chunk_by_module(code_units)
    summaries = await summarize_modules(llm, chunks, snapshot.dependency_graph)
    pattern = await detect_pattern(llm, snapshot.dependency_graph, code_units, snapshot.entry_points)
    decision_points = identify_decision_points(snapshot.dependency_graph, code_units, snapshot.entry_points)
    tradeoffs = await extract_tradeoffs(llm, decision_points, source_dir, snapshot.dependency_graph, code_units)

    # arq is at-least-once, not exactly-once — delete existing rows for this
    # snapshot before inserting, mirroring complete_snapshot's CodeUnit
    # handling (app/analysis/snapshot.py) exactly, for the same reason.
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

    await session.commit()
