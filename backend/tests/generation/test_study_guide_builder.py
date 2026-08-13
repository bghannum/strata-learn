"""study_guide_builder assembles sections from already-persisted Layer A/B
rows — so unlike test_orchestrator.py (which drives real module_summarizer/
pattern_detector/tradeoff_extractor calls), these tests insert synthetic
ModuleSummary/PatternClaim/TradeoffCard rows directly and only need the LLM
for diagram_builder's one label call. This keeps call ordering/count
predictable regardless of how many files or candidates the fixture has,
while still exercising real CodeUnit rows and a real source_dir for citation
snippet reading (via layer_a_ready_factory, Layer A only, no LLM).
"""

from pathlib import Path
from uuid import UUID

from sqlmodel import select

from app.analysis.snapshot import analyze_source, complete_snapshot, create_pending_snapshot
from app.db.models import (
    AnalysisSnapshot,
    Citation,
    CodeUnit,
    ModuleSummary,
    PatternClaim,
    Section,
    SectionType,
    StudyGuide,
    TradeoffCard,
    UnitType,
)
from app.db.session import async_session_factory
from app.generation.diagram_builder import DiagramLabelItem, DiagramLabelOutput
from app.generation.study_guide_builder import run_study_guide_generation
from app.semantics.llm_provider import FakeLLMProvider, LLMResponse

_FILES = {
    "app/__init__.py": "",
    "app/main.py": "from app.config import settings\n\n\ndef run():\n    return settings\n",
    "app/config.py": "settings = {'key': 'value'}\n",
}


def _diagram_llm() -> FakeLLMProvider:
    return FakeLLMProvider(
        [
            LLMResponse(
                text="",
                parsed=DiagramLabelOutput(
                    labels=[
                        DiagramLabelItem(file_path="app/main.py", label="App Entry Point"),
                        DiagramLabelItem(file_path="app/config.py", label="Settings"),
                    ]
                ),
                model="fake-model",
                stop_reason="end_turn",
                usage={},
            )
        ]
    )


async def _module_line_range(snapshot_id: UUID, file_path: str) -> tuple[int, int]:
    async with async_session_factory() as session:
        unit = (
            await session.exec(
                select(CodeUnit).where(
                    CodeUnit.snapshot_id == snapshot_id,
                    CodeUnit.file_path == file_path,
                    CodeUnit.unit_type == UnitType.module,
                )
            )
        ).first()
        assert unit is not None
        return unit.line_start, unit.line_end


async def _seed_layer_b(snapshot_id: UUID) -> None:
    main_start, main_end = await _module_line_range(snapshot_id, "app/main.py")
    config_start, config_end = await _module_line_range(snapshot_id, "app/config.py")

    async with async_session_factory() as session:
        session.add(
            ModuleSummary(
                snapshot_id=snapshot_id,
                file_path="app/main.py",
                purpose="Runs the application",
                role_in_system="Entry point that wires config into the app",
                key_concepts=["entry point"],
                line_start=main_start,
                line_end=main_end,
                prompt_version="v1",
                model="fake-model",
            )
        )
        session.add(
            ModuleSummary(
                snapshot_id=snapshot_id,
                file_path="app/config.py",
                purpose="Holds application configuration",
                role_in_system="Single source of truth for settings",
                key_concepts=["configuration", "settings"],
                line_start=config_start,
                line_end=config_end,
                prompt_version="v1",
                model="fake-model",
            )
        )
        session.add(
            PatternClaim(
                snapshot_id=snapshot_id,
                primary_pattern="modular monolith",
                confidence="medium",
                evidence=[
                    {
                        "claim": "settings centralized in one module",
                        "supporting_paths": ["app/config.py"],
                        "citations": [{"file_path": "app/config.py", "line_start": config_start, "line_end": config_end}],
                    }
                ],
                caveats=None,
                prompt_version="v1",
                model="fake-model",
            )
        )
        session.add(
            TradeoffCard(
                snapshot_id=snapshot_id,
                decision="centralize settings in one module",
                alternatives_considered=["scattered constants"],
                likely_reasoning="single source of truth is easier to reason about",
                tradeoff_cost="extra indirection for callers",
                confidence="medium",
                evidence_refs=[{"file_path": "app/config.py", "line_start": config_start, "line_end": config_end}],
                prompt_version="v1",
                model="fake-model",
            )
        )
        await session.commit()


async def _get_guide_with_sections(snapshot_id: UUID) -> tuple[StudyGuide, list[Section]]:
    async with async_session_factory() as session:
        guide = (await session.exec(select(StudyGuide).where(StudyGuide.snapshot_id == snapshot_id))).one()
        sections = list(
            (await session.exec(select(Section).where(Section.study_guide_id == guide.id).order_by(Section.order))).all()
        )
        return guide, sections


async def test_run_study_guide_generation_builds_all_five_sections(layer_a_ready_factory) -> None:
    repo_id, snapshot_id, source_dir = await layer_a_ready_factory(_FILES)
    await _seed_layer_b(snapshot_id)

    async with async_session_factory() as session:
        snapshot = await session.get(AnalysisSnapshot, snapshot_id)
        assert snapshot is not None

    await run_study_guide_generation(_diagram_llm(), snapshot, source_dir)

    guide, sections = await _get_guide_with_sections(snapshot_id)
    assert guide.repo_id == repo_id
    assert guide.version == 1
    assert [s.section_type for s in sections] == [
        SectionType.overview,
        SectionType.architecture,
        SectionType.tradeoffs,
        SectionType.glossary,
        SectionType.deep_dive,
    ]
    assert [s.order for s in sections] == [0, 1, 2, 3, 4]

    architecture = sections[1]
    assert "modular monolith" in architecture.content_md
    assert architecture.diagram_mermaid is not None
    assert architecture.diagram_mermaid.startswith("flowchart TD")
    assert architecture.prompt_version == "v1"

    tradeoffs = sections[2]
    assert "centralize settings in one module" in tradeoffs.content_md

    glossary = sections[3]
    assert "configuration" in glossary.content_md
    assert "settings" in glossary.content_md

    deep_dive = sections[4]
    assert "Runs the application" in deep_dive.content_md


async def test_run_study_guide_generation_persists_real_citation_snippets(layer_a_ready_factory) -> None:
    repo_id, snapshot_id, source_dir = await layer_a_ready_factory(_FILES)
    await _seed_layer_b(snapshot_id)

    async with async_session_factory() as session:
        snapshot = await session.get(AnalysisSnapshot, snapshot_id)
        assert snapshot is not None
    await run_study_guide_generation(_diagram_llm(), snapshot, source_dir)

    _guide, sections = await _get_guide_with_sections(snapshot_id)
    tradeoffs_section = next(s for s in sections if s.section_type == SectionType.tradeoffs)

    async with async_session_factory() as session:
        citations = list(
            (await session.exec(select(Citation).where(Citation.section_id == tradeoffs_section.id))).all()
        )
    assert len(citations) == 1
    assert citations[0].file_path == "app/config.py"
    assert "settings" in citations[0].snippet_text


async def test_run_study_guide_generation_is_idempotent_under_redelivery(layer_a_ready_factory) -> None:
    _repo_id, snapshot_id, source_dir = await layer_a_ready_factory(_FILES)
    await _seed_layer_b(snapshot_id)

    async with async_session_factory() as session:
        snapshot = await session.get(AnalysisSnapshot, snapshot_id)
        assert snapshot is not None

    for _ in range(2):
        await run_study_guide_generation(_diagram_llm(), snapshot, source_dir)

    async with async_session_factory() as session:
        guides = list((await session.exec(select(StudyGuide).where(StudyGuide.snapshot_id == snapshot_id))).all())
    assert len(guides) == 1
    assert guides[0].version == 1

    _guide, sections = await _get_guide_with_sections(snapshot_id)
    assert len(sections) == 5


async def test_run_study_guide_generation_increments_version_across_snapshots(
    layer_a_ready_factory, tmp_path: Path
) -> None:
    repo_id, snapshot_id_1, source_dir = await layer_a_ready_factory(_FILES)
    await _seed_layer_b(snapshot_id_1)

    async with async_session_factory() as session:
        snapshot_1 = await session.get(AnalysisSnapshot, snapshot_id_1)
        assert snapshot_1 is not None
    await run_study_guide_generation(_diagram_llm(), snapshot_1, source_dir)

    # A second snapshot for the *same* repo (re-index), reusing the same
    # source_dir — layer_a_ready_factory always creates a fresh Repo, so the
    # second snapshot is built by hand the same way that fixture does
    # internally, just against repo_id instead of a new repo.
    analysis = analyze_source(source_dir)
    async with async_session_factory() as session:
        snapshot_2 = await create_pending_snapshot(session, repo_id)
    async with async_session_factory() as session:
        snapshot_2 = await complete_snapshot(session, snapshot_2.id, None, analysis)
        assert snapshot_2 is not None
    await _seed_layer_b(snapshot_2.id)
    await run_study_guide_generation(_diagram_llm(), snapshot_2, source_dir)

    async with async_session_factory() as session:
        guide_2 = (await session.exec(select(StudyGuide).where(StudyGuide.snapshot_id == snapshot_2.id))).one()
    assert guide_2.version == 2
