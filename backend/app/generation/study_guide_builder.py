"""Assembles the study guide: Overview, Architecture, Trade-offs, Glossary,
and Deep-Dive sections, built entirely from facts Layer A/B already collected
(AnalysisSnapshot, CodeUnit, ModuleSummary, PatternClaim, TradeoffCard) — the
only new LLM call anywhere in this module is inside diagram_builder.py.

Mirrors semantics/orchestrator.py's structure: a pure build step (this
module's build_sections, plus the one diagram LLM call) followed by a short,
separate persist step, so no DB session is held open across the LLM call
(NullPool — see orchestrator.py's own comment on why that matters).
"""

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import func
from sqlmodel import delete, select

from app.db.models import (
    AnalysisSnapshot,
    Citation,
    CodeUnit,
    ModuleSummary,
    PatternClaim,
    Section,
    SectionType,
    SnapshotStatus,
    StudyGuide,
    TradeoffCard,
    UnitType,
)
from app.db.session import async_session_factory
from app.generation.citation import build_citation
from app.generation.diagram_builder import build_component_diagram
from app.semantics.llm_provider import LLMProvider


@dataclass(frozen=True)
class CitationSpec:
    file_path: str
    line_start: int
    line_end: int
    claim_excerpt: str


@dataclass(frozen=True)
class SectionSpec:
    section_type: SectionType
    title: str
    content_md: str
    citations: list[CitationSpec] = field(default_factory=list)
    diagram_mermaid: str | None = None
    prompt_version: str | None = None
    model: str | None = None


def _whole_file_citation(
    module_units_by_path: dict[str, CodeUnit], file_path: str, claim_excerpt: str
) -> CitationSpec:
    # Not every cited file has a CodeUnit (tree-sitter only parses code files —
    # an entry point like package.json or a Dockerfile won't). Anchoring to
    # line 1 there is still a true, non-fabricated citation (it correctly
    # identifies the file); it just can't claim a tighter range without
    # re-reading the file here.
    unit = module_units_by_path.get(file_path)
    line_end = unit.line_end if unit is not None else 1
    return CitationSpec(file_path=file_path, line_start=1, line_end=line_end, claim_excerpt=claim_excerpt)


def _build_overview(snapshot: AnalysisSnapshot, module_units_by_path: dict[str, CodeUnit]) -> SectionSpec:
    lang_line = ", ".join(f"{lang} ({count} files)" for lang, count in sorted(snapshot.language_summary.items()))
    lines = ["## Tech Stack", "", f"Languages: {lang_line or 'none detected'}.", "", "## Entry Points", ""]

    citations = []
    for ep in snapshot.entry_points:
        reason = ep.get("reason", "")
        lines.append(f"- **{ep['file']}** ({ep['kind']}): {reason}")
        citations.append(_whole_file_citation(module_units_by_path, ep["file"], reason or ep["file"]))

    return SectionSpec(
        section_type=SectionType.overview,
        title="Overview",
        content_md="\n".join(lines),
        citations=citations,
    )


def _build_architecture(
    pattern_claim: PatternClaim | None, diagram_mermaid: str | None, diagram_prompt_version: str | None,
    diagram_model: str | None, diagram_citations: list[CitationSpec],
) -> SectionSpec | None:
    if pattern_claim is None and diagram_mermaid is None:
        return None

    lines: list[str] = []
    citations = list(diagram_citations)
    if pattern_claim is not None:
        lines.append(f"**Primary pattern:** {pattern_claim.primary_pattern} (confidence: {pattern_claim.confidence})")
        if pattern_claim.caveats:
            lines += ["", pattern_claim.caveats]
        lines += ["", "**Evidence:**", ""]
        for item in pattern_claim.evidence:
            paths = ", ".join(f"`{p}`" for p in item.get("supporting_paths", []))
            lines.append(f"- {item['claim']} — {paths}")
            for cite in item.get("citations", []):
                citations.append(
                    CitationSpec(
                        file_path=cite["file_path"],
                        line_start=cite["line_start"],
                        line_end=cite["line_end"],
                        claim_excerpt=item["claim"],
                    )
                )

    return SectionSpec(
        section_type=SectionType.architecture,
        title="Architecture",
        content_md="\n".join(lines) if lines else "_No architecture pattern could be grounded in evidence._",
        citations=citations,
        diagram_mermaid=diagram_mermaid,
        prompt_version=diagram_prompt_version,
        model=diagram_model,
    )


def _build_tradeoffs(tradeoff_cards: list[TradeoffCard]) -> SectionSpec | None:
    if not tradeoff_cards:
        return None

    lines: list[str] = []
    citations = []
    for card in sorted(tradeoff_cards, key=lambda c: c.decision):
        lines += [
            f"### {card.decision}",
            "",
            f"**Reasoning:** {card.likely_reasoning}",
            "",
            f"**Trade-off:** {card.tradeoff_cost}",
            "",
            f"**Alternatives considered:** {', '.join(card.alternatives_considered) or 'none recorded'}",
            "",
            f"**Confidence:** {card.confidence}",
            "",
        ]
        for ref in card.evidence_refs:
            citations.append(
                CitationSpec(
                    file_path=ref["file_path"],
                    line_start=ref["line_start"],
                    line_end=ref["line_end"],
                    claim_excerpt=card.decision,
                )
            )

    return SectionSpec(
        section_type=SectionType.tradeoffs,
        title="Trade-offs",
        content_md="\n".join(lines).rstrip(),
        citations=citations,
    )


def _build_glossary(module_summaries: list[ModuleSummary]) -> SectionSpec | None:
    if not module_summaries:
        return None

    # First module (by file_path) to mention a concept is credited as its
    # source citation — a concept repeated across many files would otherwise
    # need one citation per mention, which the Section/Citation schema
    # doesn't need for a glossary entry.
    first_source: dict[str, ModuleSummary] = {}
    for summary in sorted(module_summaries, key=lambda s: s.file_path):
        for concept in summary.key_concepts:
            first_source.setdefault(concept, summary)

    lines = []
    citations = []
    for concept in sorted(first_source, key=str.casefold):
        summary = first_source[concept]
        lines.append(f"- **{concept}** — introduced in `{summary.file_path}`")
        citations.append(
            CitationSpec(
                file_path=summary.file_path,
                line_start=summary.line_start,
                line_end=summary.line_end,
                claim_excerpt=f"Key concept: {concept}",
            )
        )

    return SectionSpec(
        section_type=SectionType.glossary, title="Glossary", content_md="\n".join(lines), citations=citations
    )


def _build_deep_dives(module_summaries: list[ModuleSummary]) -> SectionSpec | None:
    if not module_summaries:
        return None

    lines = []
    citations = []
    for summary in sorted(module_summaries, key=lambda s: (s.file_path, s.line_start)):
        lines += [
            f"### `{summary.file_path}` (lines {summary.line_start}-{summary.line_end})",
            "",
            summary.purpose,
            "",
            summary.role_in_system,
            "",
        ]
        citations.append(
            CitationSpec(
                file_path=summary.file_path,
                line_start=summary.line_start,
                line_end=summary.line_end,
                claim_excerpt=summary.purpose,
            )
        )

    return SectionSpec(
        section_type=SectionType.deep_dive, title="Deep Dives", content_md="\n".join(lines).rstrip(), citations=citations
    )


async def build_sections(
    llm: LLMProvider,
    snapshot: AnalysisSnapshot,
    code_units: list[CodeUnit],
    module_summaries: list[ModuleSummary],
    pattern_claim: PatternClaim | None,
    tradeoff_cards: list[TradeoffCard],
) -> list[SectionSpec]:
    module_units_by_path = {u.file_path: u for u in code_units if u.unit_type == UnitType.module}
    module_purposes: dict[str, str] = {}
    for summary in module_summaries:
        module_purposes.setdefault(summary.file_path, summary.purpose)

    diagram = await build_component_diagram(llm, snapshot.dependency_graph, module_purposes)
    diagram_citations = (
        [
            _whole_file_citation(module_units_by_path, path, "Included in the architecture diagram")
            for path in diagram.file_paths
        ]
        if diagram is not None
        else []
    )

    candidates = [
        _build_overview(snapshot, module_units_by_path),
        _build_architecture(
            pattern_claim,
            diagram.mermaid if diagram is not None else None,
            diagram.prompt_version if diagram is not None else None,
            diagram.model if diagram is not None else None,
            diagram_citations,
        ),
        _build_tradeoffs(tradeoff_cards),
        _build_glossary(module_summaries),
        _build_deep_dives(module_summaries),
    ]
    return [section for section in candidates if section is not None]


async def _next_version(session, repo_id, existing_guide: StudyGuide | None) -> int:
    if existing_guide is not None:
        # Same snapshot regenerated (arq redelivery) — reuse its version
        # rather than incrementing, matching orchestrator.py's delete-then-
        # insert idempotency for the same reason: at-least-once delivery.
        return existing_guide.version
    max_version = (await session.exec(select(func.max(StudyGuide.version)).where(StudyGuide.repo_id == repo_id))).one()
    return (max_version or 0) + 1


async def persist_study_guide(snapshot: AnalysisSnapshot, sections: list[SectionSpec], source_dir: Path) -> None:
    async with async_session_factory() as session:
        existing_guide = (
            await session.exec(select(StudyGuide).where(StudyGuide.snapshot_id == snapshot.id))
        ).first()
        version = await _next_version(session, snapshot.repo_id, existing_guide)

        if existing_guide is not None:
            existing_section_ids = (
                await session.exec(select(Section.id).where(Section.study_guide_id == existing_guide.id))
            ).all()
            if existing_section_ids:
                await session.exec(delete(Citation).where(Citation.section_id.in_(existing_section_ids)))
            await session.exec(delete(Section).where(Section.study_guide_id == existing_guide.id))
            await session.exec(delete(StudyGuide).where(StudyGuide.id == existing_guide.id))

        guide = StudyGuide(repo_id=snapshot.repo_id, snapshot_id=snapshot.id, version=version)
        session.add(guide)
        await session.flush()  # assigns guide.id without committing yet

        for order, spec in enumerate(sections):
            section = Section(
                study_guide_id=guide.id,
                section_type=spec.section_type,
                title=spec.title,
                order=order,
                content_md=spec.content_md,
                diagram_mermaid=spec.diagram_mermaid,
                prompt_version=spec.prompt_version,
                model=spec.model,
            )
            session.add(section)
            await session.flush()  # assigns section.id

            for cite_spec in spec.citations:
                cite = build_citation(
                    source_dir, cite_spec.file_path, cite_spec.line_start, cite_spec.line_end, cite_spec.claim_excerpt
                )
                session.add(
                    Citation(
                        section_id=section.id,
                        file_path=cite.file_path,
                        line_start=cite.line_start,
                        line_end=cite.line_end,
                        claim_excerpt=cite.claim_excerpt,
                        snippet_text=cite.snippet_text,
                    )
                )

        # Same commit as the generated rows, not a later separate one — same
        # invariant orchestrator.py's run_layer_b already established (see
        # its comment): a worker crash between a separate status commit and
        # this one would leave a redelivery-visible window where the data is
        # persisted but the snapshot still reads `generating`.
        current = await session.get(AnalysisSnapshot, snapshot.id)
        if current is not None:
            current.status = SnapshotStatus.ready
            session.add(current)

        await session.commit()


async def run_study_guide_generation(llm: LLMProvider, snapshot: AnalysisSnapshot, source_dir: Path) -> None:
    async with async_session_factory() as session:
        code_units = list(
            (await session.exec(select(CodeUnit).where(CodeUnit.snapshot_id == snapshot.id))).all()
        )
        module_summaries = list(
            (await session.exec(select(ModuleSummary).where(ModuleSummary.snapshot_id == snapshot.id))).all()
        )
        pattern_claim = (
            await session.exec(select(PatternClaim).where(PatternClaim.snapshot_id == snapshot.id))
        ).first()
        tradeoff_cards = list(
            (await session.exec(select(TradeoffCard).where(TradeoffCard.snapshot_id == snapshot.id))).all()
        )

    sections = await build_sections(llm, snapshot, code_units, module_summaries, pattern_claim, tradeoff_cards)
    await persist_study_guide(snapshot, sections, source_dir)
