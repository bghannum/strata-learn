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
from uuid import UUID, uuid4

from sqlmodel import select

from app.analysis.snapshot import analyze_source, complete_snapshot, create_pending_snapshot
from app.db.models import (
    AnalysisSnapshot,
    Citation,
    CodeUnit,
    Confidence,
    ModuleSummary,
    PatternClaim,
    Section,
    SectionType,
    StudyGuide,
    Subsystem,
    TradeoffCard,
    UnitType,
)
from app.db.session import async_session_factory
from app.generation.architecture_narrative import ArchitectureNarrativeOutput, WhySection
from app.generation.diagram_builder import DiagramLabelItem, DiagramLabelOutput
from app.generation.study_guide_builder import (
    MAX_OVERVIEW_ENTRY_POINTS,
    _build_architecture,
    _build_deep_dives,
    _build_overview,
    _whole_file_citation,
    run_study_guide_generation,
)
from app.semantics.llm_provider import FakeLLMProvider, LLMResponse

_FILES = {
    "app/__init__.py": "",
    "app/main.py": "from app.config import settings\n\n\ndef run():\n    return settings\n",
    "app/config.py": "settings = {'key': 'value'}\n",
}


def _diagram_llm() -> FakeLLMProvider:
    """The narrative call runs first, then the diagram labeler — build_sections
    synthesizes before it draws."""
    return FakeLLMProvider(
        [
            LLMResponse(
                text="",
                parsed=ArchitectureNarrativeOutput(
                    overview="The app takes a request and hands the slow work to a worker.",
                    why_sections=[
                        WhySection(
                            heading="Why configuration lives in one module",
                            body="Every caller reads the same settings object instead of its own environment.",
                            supporting_paths=["app/config.py"],
                        )
                    ],
                ),
                model="fake-model",
                stop_reason="end_turn",
                usage={},
            ),
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


def test_whole_file_citation_uses_real_line_count_when_no_code_unit(tmp_path: Path) -> None:
    # package.json/Dockerfile entry points have no CodeUnit (tree-sitter only
    # parses code files). A pretty-printed package.json's line 1 is just
    # `{`, nowhere near an entry point's actual "main" field further down —
    # the whole-file fallback must cover the real file, not a hardcoded
    # line_end=1 that only ever points at the opening brace.
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "package.json").write_text('{\n  "name": "x",\n  "main": "index.js"\n}\n')

    citation = _whole_file_citation({}, source_dir, "package.json", "claim")

    assert citation.line_start == 1
    assert citation.line_end == 4


def test_build_overview_caps_entry_points(tmp_path: Path) -> None:
    # git_url ingestion has no file-count cap, so a large repo could detect
    # far more entry points than are useful to render or worth citing.
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    for i in range(40):
        (source_dir / f"file{i}.py").write_text("if __name__ == '__main__':\n    pass\n")
    entry_points = [{"file": f"file{i}.py", "kind": "cli", "reason": "guard"} for i in range(40)]
    snapshot = AnalysisSnapshot(repo_id=uuid4(), language_summary={}, entry_points=entry_points)

    section = _build_overview(snapshot, {}, source_dir)

    assert len(section.citations) == MAX_OVERVIEW_ENTRY_POINTS
    assert section.content_md.count("- **file") == MAX_OVERVIEW_ENTRY_POINTS
    assert "10 more, not shown" in section.content_md


def test_build_deep_dives_orders_split_file_parts_by_chunk_index() -> None:
    # #14: every chunk of a split file carries the same whole-module line range,
    # so the previous sort key (line_start) was identical across them and the
    # "Part N of M" labels were assigned in whatever order Postgres returned
    # the rows. Passing them in reversed order must still render 1 then 2.
    def _summary(chunk_index: int, purpose: str) -> ModuleSummary:
        return ModuleSummary(
            snapshot_id=uuid4(),
            file_path="app/big.py",
            purpose=purpose,
            role_in_system="part of the big module",
            key_concepts=[],
            line_start=1,
            line_end=900,
            chunk_index=chunk_index,
            chunk_count=2,
            prompt_version="v1",
            model="fake-model",
        )

    section = _build_deep_dives([_summary(2, "Second half"), _summary(1, "First half")])

    assert section is not None
    assert section.content_md.index("**Part 1 of 2:**") < section.content_md.index("**Part 2 of 2:**")
    assert section.content_md.index("First half") < section.content_md.index("Second half")


def test_build_deep_dives_omits_part_labels_for_a_whole_file_summary() -> None:
    summary = ModuleSummary(
        snapshot_id=uuid4(),
        file_path="app/main.py",
        purpose="Runs the application",
        role_in_system="Entry point",
        key_concepts=[],
        line_start=1,
        line_end=20,
        prompt_version="v1",
        model="fake-model",
    )

    section = _build_deep_dives([summary])

    assert section is not None
    assert "Part 1 of 1" not in section.content_md


def _deep_dive_summary(file_path: str, purpose: str) -> ModuleSummary:
    return ModuleSummary(
        snapshot_id=uuid4(),
        file_path=file_path,
        purpose=purpose,
        role_in_system="does its part",
        key_concepts=[],
        line_start=1,
        line_end=20,
        prompt_version="v1",
        model="fake-model",
    )


def _deep_dive_subsystem(name: str, file_paths: list[str], order: int) -> Subsystem:
    return Subsystem(
        snapshot_id=uuid4(),
        key=name.lower(),
        name=name,
        role=f"the {name} part",
        file_paths=file_paths,
        depth=order,
        order=order,
        prompt_version="v1",
        model="fake-model",
    )


def test_build_deep_dives_groups_by_subsystem_in_partition_order() -> None:
    # #54: a flat alphabetical file list is a reference index — it gives a
    # reader no way to see which files form a unit or what order to read them.
    summaries = [
        _deep_dive_summary("app/worker/tasks.py", "Runs jobs"),
        _deep_dive_summary("app/api/repos.py", "Serves repos"),
    ]
    subsystems = [
        _deep_dive_subsystem("HTTP API", ["app/api/repos.py"], order=0),
        _deep_dive_subsystem("Background worker", ["app/worker/tasks.py"], order=1),
    ]

    section = _build_deep_dives(summaries, subsystems)

    assert section is not None
    # "Background worker" sorts before "HTTP API" alphabetically, so the
    # partition's order is doing real work here rather than coinciding
    assert section.content_md.index("## HTTP API") < section.content_md.index("## Background worker")
    assert "the HTTP API part" in section.content_md


def test_build_deep_dives_puts_unclaimed_files_under_other() -> None:
    # A snapshot indexed before subsystems existed, or a file no subsystem
    # claims — it must still appear. Silently dropping files from the deep
    # dives would be worse than grouping them imperfectly.
    summaries = [_deep_dive_summary("app/api/repos.py", "Serves repos"), _deep_dive_summary("stray.py", "Strays")]
    subsystems = [_deep_dive_subsystem("HTTP API", ["app/api/repos.py"], order=0)]

    section = _build_deep_dives(summaries, subsystems)

    assert section is not None
    assert "## Other" in section.content_md
    assert section.content_md.index("## HTTP API") < section.content_md.index("## Other")
    assert "`stray.py`" in section.content_md


def test_build_deep_dives_skips_a_subsystem_with_no_summarized_files() -> None:
    summaries = [_deep_dive_summary("app/api/repos.py", "Serves repos")]
    subsystems = [
        _deep_dive_subsystem("HTTP API", ["app/api/repos.py"], order=0),
        _deep_dive_subsystem("Empty", ["app/never/summarized.py"], order=1),
    ]

    section = _build_deep_dives(summaries, subsystems)

    assert section is not None
    assert "## Empty" not in section.content_md


def test_build_deep_dives_without_subsystems_stays_flat() -> None:
    # Backward compatibility for a snapshot with no Subsystem rows: no empty
    # "Other" heading wrapped around what is already a flat list.
    section = _build_deep_dives([_deep_dive_summary("app/main.py", "Runs it")], [])

    assert section is not None
    # no subsystem headings (## ...); the per-file headings (### ...) remain
    assert not [line for line in section.content_md.splitlines() if line.startswith("## ")]
    assert "`app/main.py`" in section.content_md


def test_build_architecture_renders_evidence_plainly_without_a_narrative() -> None:
    # If the narrative call produced nothing, the evidence must not be
    # collapsed behind a disclosure the reader has to find and open — it's the
    # whole section at that point.
    claim = PatternClaim(
        snapshot_id=uuid4(),
        primary_pattern="modular monolith",
        confidence=Confidence.medium,
        evidence=[{"claim": "one app package", "supporting_paths": ["app/main.py"], "citations": []}],
        caveats=None,
        prompt_version="v1",
        model="fake-model",
    )

    section = _build_architecture(claim, None, None, None, [], None)

    assert section is not None
    assert "<details>" not in section.content_md
    assert section.content_md.startswith("**Primary pattern:**")


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
    assert "confidence: medium" in architecture.content_md
    assert "Confidence." not in architecture.content_md  # not the raw enum name

    # #52: the narrative leads and the evidence list is demoted, rather than
    # the section being a pattern label plus bullets.
    assert architecture.content_md.startswith("The app takes a request")
    assert "### Why configuration lives in one module" in architecture.content_md
    assert architecture.content_md.index("The app takes a request") < architecture.content_md.index(
        "**Primary pattern:**"
    )
    assert "<details>" in architecture.content_md

    tradeoffs = sections[2]
    assert "centralize settings in one module" in tradeoffs.content_md
    assert "**Confidence:** medium" in tradeoffs.content_md
    assert "Confidence." not in tradeoffs.content_md

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
    # claim_excerpt covers reasoning + cost too, not just the decision
    # headline — evidence_refs ground the whole card, not only its title.
    assert "single source of truth" in citations[0].claim_excerpt
    assert "extra indirection" in citations[0].claim_excerpt

    deep_dive_section = next(s for s in sections if s.section_type == SectionType.deep_dive)
    async with async_session_factory() as session:
        deep_dive_citations = list(
            (await session.exec(select(Citation).where(Citation.section_id == deep_dive_section.id))).all()
        )
    main_citation = next(c for c in deep_dive_citations if c.file_path == "app/main.py")
    # claim_excerpt covers role_in_system too, not just purpose.
    assert "Runs the application" in main_citation.claim_excerpt
    assert "Entry point that wires config" in main_citation.claim_excerpt

    architecture_section = next(s for s in sections if s.section_type == SectionType.architecture)
    async with async_session_factory() as session:
        architecture_citations = list(
            (await session.exec(select(Citation).where(Citation.section_id == architecture_section.id))).all()
        )
    # claim_excerpt covers the primary_pattern headline, not just the
    # evidence item's own sentence.
    assert any("modular monolith" in c.claim_excerpt for c in architecture_citations)


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
