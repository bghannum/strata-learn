"""Assembles the study guide: Overview, Architecture, Trade-offs, Glossary,
and Deep-Dive sections, built from facts Layer A/B already collected
(AnalysisSnapshot, CodeUnit, ModuleSummary, PatternClaim, TradeoffCard,
Subsystem).

Two LLM calls are made from here: diagram_builder.py's node labeling, and —
as of Phase 6 — architecture_narrative.py's synthesis pass. Everything else is
still deterministic assembly of already-persisted rows. The narrative is the
deliberate exception: with no synthesis anywhere, the Architecture section could
only ever be a pattern label plus a citation list, which is exactly how it read
(#52).

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
    Subsystem,
    TradeoffCard,
    UnitType,
)
from app.db.session import async_session_factory
from app.generation.architecture_narrative import (
    ArchitectureNarrative,
    build_architecture_narrative,
    narrative_from_payload,
    narrative_payload,
)
from app.generation.artifact_cache import ARCHITECTURE_NARRATIVE, COMPONENT_DIAGRAM, load_artifact, save_artifact
from app.generation.citation import read_snippet
from app.generation.diagram_builder import build_component_diagram, diagram_from_payload, diagram_payload
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


# git_url ingestion has no file-count cap (only zip uploads are capped, at
# settings.zip_upload_max_files — see pattern_detector.py's own MAX_GRAPH_NODES
# comment for the same gap), so entry_points can be arbitrarily large on a
# big repo. Each one gets a whole-file citation (up to MAX_SNIPPET_CHARS,
# citation.py) — uncapped, that's an unbounded number of them, read and
# persisted, then all returned by GET /study-guides/{id} in one response
# (found via Codex's Phase 3 pre-push review). Also just more useful: an
# Overview section with hundreds of bullet points isn't a readable overview.
MAX_OVERVIEW_ENTRY_POINTS = 30


def _file_line_count(source_dir: Path, file_path: str) -> int:
    try:
        text = (source_dir / file_path).read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return 1
    return max(1, len(text.splitlines()))


def _whole_file_citation(
    module_units_by_path: dict[str, CodeUnit], source_dir: Path, file_path: str, claim_excerpt: str
) -> CitationSpec:
    # Not every cited file has a CodeUnit (tree-sitter only parses code files —
    # an entry point like package.json or a Dockerfile won't). Citing the
    # whole file there is still a true, non-fabricated citation — but the
    # whole file means line 1 through the real last line, not a hardcoded
    # line_end=1: for a typical pretty-printed package.json or Dockerfile,
    # line 1 alone is just `{` or the first `FROM`, nowhere near the actual
    # main/CMD content the claim describes (found via Codex's Phase 3
    # pre-push review).
    unit = module_units_by_path.get(file_path)
    line_end = unit.line_end if unit is not None else _file_line_count(source_dir, file_path)
    return CitationSpec(file_path=file_path, line_start=1, line_end=line_end, claim_excerpt=claim_excerpt)


def _build_overview(
    snapshot: AnalysisSnapshot, module_units_by_path: dict[str, CodeUnit], source_dir: Path
) -> SectionSpec:
    lang_line = ", ".join(f"{lang} ({count} files)" for lang, count in sorted(snapshot.language_summary.items()))
    lines = ["## Tech Stack", "", f"Languages: {lang_line or 'none detected'}.", "", "## Entry Points", ""]

    entry_points = sorted(snapshot.entry_points, key=lambda ep: (ep["file"], ep["kind"]))
    citations = []
    for ep in entry_points[:MAX_OVERVIEW_ENTRY_POINTS]:
        reason = ep.get("reason", "")
        lines.append(f"- **{ep['file']}** ({ep['kind']}): {reason}")
        citations.append(_whole_file_citation(module_units_by_path, source_dir, ep["file"], reason or ep["file"]))
    if len(entry_points) > MAX_OVERVIEW_ENTRY_POINTS:
        lines.append(f"- _...and {len(entry_points) - MAX_OVERVIEW_ENTRY_POINTS} more, not shown._")

    return SectionSpec(
        section_type=SectionType.overview,
        title="Overview",
        content_md="\n".join(lines),
        citations=citations,
    )


def _build_architecture(
    pattern_claim: PatternClaim | None, diagram_mermaid: str | None, diagram_prompt_version: str | None,
    diagram_model: str | None, diagram_citations: list[CitationSpec],
    narrative: ArchitectureNarrative | None = None,
) -> SectionSpec | None:
    if pattern_claim is None and diagram_mermaid is None and narrative is None:
        return None

    lines: list[str] = []
    citations = list(diagram_citations)

    # Narrative first, evidence second (#52). The pattern label, confidence,
    # and per-claim evidence bullets below used to *be* the section; they're
    # still here and still cited, just no longer the thing a reader meets
    # first. Collapsed into a <details> block so the section opens as an
    # explanation rather than as a citation list — the markdown renderer
    # passes the HTML through, and a reader with it closed still gets every
    # claim the old section made.
    if narrative is not None:
        if narrative.overview:
            lines += [narrative.overview, ""]
        for section in narrative.why_sections:
            lines += [f"### {section.heading}", "", section.body, ""]
        for cite in narrative.citations:
            citations.append(
                CitationSpec(
                    file_path=cite.file_path,
                    line_start=cite.line_start,
                    line_end=cite.line_end,
                    claim_excerpt=cite.claim_excerpt,
                )
            )

    evidence_lines: list[str] = []
    if pattern_claim is not None:
        # .value, not the bare enum — Confidence is `str, Enum`, but Python's
        # default Enum.__str__ still wins over the str mixin in f-strings,
        # rendering "Confidence.medium" instead of "medium" (confirmed via
        # Codex's Phase 3 pre-push review, and visible in the Phase 3 manual
        # checkpoint's own output).
        evidence_lines.append(
            f"**Primary pattern:** {pattern_claim.primary_pattern} (confidence: {pattern_claim.confidence.value})"
        )
        if pattern_claim.caveats:
            evidence_lines += ["", pattern_claim.caveats]
        evidence_lines += ["", "**Evidence:**", ""]
        # The rendered headline (primary_pattern + caveats) is the claim each
        # evidence item's citations actually support — the LLM call that
        # produced them evaluated the whole graph to reach that one claim,
        # not just the item's own sentence. claim_excerpt names both so a
        # citation lookup by claim_excerpt covers the full rendered prose,
        # not only the bullet (found via Codex's Phase 3 pre-push review:
        # citing only item['claim'] left primary_pattern/caveats with no
        # citation of their own).
        primary_claim_text = pattern_claim.primary_pattern
        if pattern_claim.caveats:
            primary_claim_text += f" {pattern_claim.caveats}"
        for item in pattern_claim.evidence:
            paths = ", ".join(f"`{p}`" for p in item.get("supporting_paths", []))
            evidence_lines.append(f"- {item['claim']} — {paths}")
            for cite in item.get("citations", []):
                citations.append(
                    CitationSpec(
                        file_path=cite["file_path"],
                        line_start=cite["line_start"],
                        line_end=cite["line_end"],
                        claim_excerpt=f"{primary_claim_text} — {item['claim']}",
                    )
                )

    if evidence_lines:
        if narrative is None:
            # No narrative to lead with (the LLM call failed its own
            # validation, or there was nothing to synthesize from) — render the
            # evidence plainly rather than collapsing the entire section behind
            # a disclosure the reader has to find and open.
            lines += evidence_lines
        else:
            lines += [
                "<details>",
                f"<summary>Evidence ({len(pattern_claim.evidence)} claims)</summary>",
                "",
                *evidence_lines,
                "",
                "</details>",
            ]

    return SectionSpec(
        section_type=SectionType.architecture,
        title="Architecture",
        content_md="\n".join(lines).strip() if lines else "_No architecture pattern could be grounded in evidence._",
        citations=citations,
        diagram_mermaid=diagram_mermaid,
        # Two LLM calls now feed this section (the diagram labeler and the
        # narrative). Section has one prompt_version/model pair, so it records
        # the narrative's — the call that produced the section's actual prose.
        # The diagram's own provenance stays reachable through its citations.
        prompt_version=narrative.prompt_version if narrative is not None else diagram_prompt_version,
        model=narrative.model if narrative is not None else diagram_model,
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
            f"**Confidence:** {card.confidence.value}",  # .value — see the Architecture section's comment
            "",
        ]
        # claim_excerpt covers every rendered generated field except
        # confidence (self-reported certainty, not itself a claim about the
        # code) — evidence_refs were validated against the same LLM call
        # that produced decision/reasoning/cost/alternatives together
        # (tradeoff_extractor.py), so they ground the whole card (found via
        # Codex's Phase 3 pre-push review).
        alternatives = ", ".join(card.alternatives_considered) or "none recorded"
        claim_excerpt = (
            f"{card.decision} — {card.likely_reasoning} "
            f"Trade-off: {card.tradeoff_cost} Alternatives considered: {alternatives}"
        )
        for ref in card.evidence_refs:
            citations.append(
                CitationSpec(
                    file_path=ref["file_path"],
                    line_start=ref["line_start"],
                    line_end=ref["line_end"],
                    claim_excerpt=claim_excerpt,
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

    # First module (by file_path, then line_start for a file split into
    # multiple chunks — see _build_deep_dives) to mention a concept is
    # credited as its source citation — a concept repeated across many files
    # would otherwise need one citation per mention, which the
    # Section/Citation schema doesn't need for a glossary entry.
    first_source: dict[str, ModuleSummary] = {}
    for summary in sorted(module_summaries, key=lambda s: (s.file_path, s.line_start)):
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


# Files whose subsystem was never persisted (a snapshot indexed before
# subsystems existed, or a file with no module summary in any group) still have
# to appear — a deep dive silently missing files would be worse than an
# imperfectly grouped one.
_UNGROUPED_HEADING = "Other"


def _subsystem_groups(
    file_paths: set[str], subsystems: list[Subsystem]
) -> list[tuple[str, str, list[str]]]:
    """(heading, role, file_paths) per subsystem, in the partition's own
    outside-in order, with anything left over trailing under "Other"."""
    groups: list[tuple[str, str, list[str]]] = []
    claimed: set[str] = set()
    for subsystem in sorted(subsystems, key=lambda s: s.order):
        members = sorted(p for p in subsystem.file_paths if p in file_paths)
        if not members:
            continue  # every file in it was skipped by Layer B — no heading to render
        claimed.update(members)
        groups.append((subsystem.name, subsystem.role, members))

    leftover = sorted(file_paths - claimed)
    if leftover:
        groups.append((_UNGROUPED_HEADING, "", leftover))
    return groups


def _build_deep_dives(
    module_summaries: list[ModuleSummary], subsystems: list[Subsystem] | None = None
) -> SectionSpec | None:
    if not module_summaries:
        return None

    # A file with more class/function units than MAX_UNITS_PER_CHUNK
    # (chunking.py) gets split into multiple ModuleSummary rows, each
    # covering the *same* whole-file line range (chunk.module_unit is shared
    # across a file's chunks, on purpose, for grounding context —
    # module_summarizer.py) but describing a different subset of the file's
    # units, so their purpose/role_in_system can genuinely differ. Grouping
    # by file_path keeps one heading per file instead of repeating it once
    # per chunk with conflicting text (found via Codex's Phase 3 pre-push
    # review).
    by_file: dict[str, list[ModuleSummary]] = {}
    for summary in module_summaries:
        by_file.setdefault(summary.file_path, []).append(summary)

    # Grouped by subsystem rather than one flat alphabetical list of files
    # (#54): a file listing is a reference index, and a reader who doesn't
    # already know the codebase can't tell from it which files form a unit or
    # what order to read them in. Only grouping and ordering change here — the
    # per-file text and its citations are untouched, since the conceptual "why"
    # writing lives in the Architecture narrative and duplicating it here would
    # mean a second synthesis pass for marginal gain.
    groups = _subsystem_groups(set(by_file), subsystems or [])

    lines: list[str] = []
    citations = []
    for heading, role, file_paths in groups:
        if subsystems:
            lines += [f"## {heading}", ""]
            if role:
                lines += [role, ""]
        for file_path in file_paths:
            # Ordered by chunk_index, not line_start: every chunk of a file carries
            # the identical whole-module line range, so the previous line_start sort
            # had no real key to sort on and the "Part N of M" labels were assigned
            # in whatever order Postgres returned the rows (#14, fixed by persisting
            # the index rather than trying to recover it here).
            chunks = sorted(by_file[file_path], key=lambda s: s.chunk_index)
            lines += [f"### `{file_path}` (lines {chunks[0].line_start}-{chunks[0].line_end})", ""]
            for summary in chunks:
                if len(chunks) > 1:
                    lines.append(f"**Part {summary.chunk_index} of {summary.chunk_count}:**")
                lines += [summary.purpose, "", summary.role_in_system, ""]
                # Both rendered sentences, not just purpose — they come from the
                # same LLM call grounded in the same line range, so both are
                # covered by this one citation (found via Codex's Phase 3
                # pre-push review: citing only purpose left role_in_system with
                # no citation).
                citations.append(
                    CitationSpec(
                        file_path=summary.file_path,
                        line_start=summary.line_start,
                        line_end=summary.line_end,
                        claim_excerpt=f"{summary.purpose} {summary.role_in_system}",
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
    source_dir: Path,
    subsystems: list[Subsystem] | None = None,
) -> list[SectionSpec]:
    subsystems = subsystems or []
    module_units_by_path = {u.file_path: u for u in code_units if u.unit_type == UnitType.module}
    module_purposes: dict[str, str] = {}
    for summary in module_summaries:
        module_purposes.setdefault(summary.file_path, summary.purpose)

    # Both calls are cached against the snapshot (#23): a crash between
    # generating one and persist_study_guide's commit used to rebill it on the
    # next redelivery, and the narrative runs on the strongest model tier with
    # the largest prompt in the system. Load-before / save-after, each in its
    # own short session — no session is alive while a call is in flight.
    cached_narrative = await load_artifact(snapshot.id, ARCHITECTURE_NARRATIVE)
    if cached_narrative is not None:
        narrative = narrative_from_payload(
            cached_narrative.payload, cached_narrative.prompt_version, cached_narrative.model
        )
    else:
        narrative = await build_architecture_narrative(
            llm, pattern_claim, subsystems, tradeoff_cards, snapshot.entry_points, code_units
        )
        if narrative is not None:
            await save_artifact(
                snapshot.id,
                ARCHITECTURE_NARRATIVE,
                narrative_payload(narrative),
                narrative.prompt_version,
                narrative.model,
            )

    cached_diagram = await load_artifact(snapshot.id, COMPONENT_DIAGRAM)
    if cached_diagram is not None:
        diagram = diagram_from_payload(
            cached_diagram.payload, cached_diagram.prompt_version, cached_diagram.model
        )
    else:
        diagram = await build_component_diagram(llm, snapshot.dependency_graph, module_purposes)
        if diagram is not None:
            await save_artifact(
                snapshot.id, COMPONENT_DIAGRAM, diagram_payload(diagram), diagram.prompt_version, diagram.model
            )
    # claim_excerpt names the actual generated label ("HTTP API routes"),
    # not just "included in the diagram" — the label itself is the LLM's
    # claim about this file's role, so the citation should say what it's
    # grounding (found via Codex's Phase 3 pre-push review).
    diagram_citations = (
        [
            _whole_file_citation(
                module_units_by_path, source_dir, path, f"Diagram label: {diagram.labels[path]}"
            )
            for path in diagram.file_paths
        ]
        if diagram is not None
        else []
    )

    candidates = [
        _build_overview(snapshot, module_units_by_path, source_dir),
        _build_architecture(
            pattern_claim,
            diagram.mermaid if diagram is not None else None,
            diagram.prompt_version if diagram is not None else None,
            diagram.model if diagram is not None else None,
            diagram_citations,
            narrative,
        ),
        _build_tradeoffs(tradeoff_cards),
        _build_glossary(module_summaries),
        _build_deep_dives(module_summaries, subsystems),
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
    # Read every cited snippet before opening the write transaction below —
    # NullPool means every session checkout is a real Postgres connection,
    # and synchronous file I/O for potentially many citations (deep-dive and
    # glossary citations both reuse a module's whole-file line range, so
    # this also naturally dedupes repeated reads of the same range) has no
    # business happening while one is held open (found via Codex's Phase 3
    # pre-push review — the same class of concern as orchestrator.py's "no
    # session across slow work" rule, just for disk reads instead of LLM
    # calls). MAX_SNIPPET_LINES/MAX_SNIPPET_CHARS (citation.py) bound each
    # entry's size; this cache only avoids redundant reads of the same range.
    snippet_cache: dict[tuple[str, int, int], str] = {}
    for spec in sections:
        for cite_spec in spec.citations:
            key = (cite_spec.file_path, cite_spec.line_start, cite_spec.line_end)
            if key not in snippet_cache:
                snippet_cache[key] = read_snippet(source_dir, *key)

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
                key = (cite_spec.file_path, cite_spec.line_start, cite_spec.line_end)
                if key not in snippet_cache:
                    snippet_cache[key] = read_snippet(source_dir, *key)
                session.add(
                    Citation(
                        section_id=section.id,
                        file_path=cite_spec.file_path,
                        line_start=cite_spec.line_start,
                        line_end=cite_spec.line_end,
                        claim_excerpt=cite_spec.claim_excerpt,
                        snippet_text=snippet_cache[key],
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
        subsystems = list(
            (await session.exec(select(Subsystem).where(Subsystem.snapshot_id == snapshot.id))).all()
        )

    sections = await build_sections(
        llm, snapshot, code_units, module_summaries, pattern_claim, tradeoff_cards, source_dir, subsystems
    )
    await persist_study_guide(snapshot, sections, source_dir)
