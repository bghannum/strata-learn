"""GET /study-guides/{id}: the assembled study guide with its ordered
sections and each section's citations — the read side of
generation/study_guide_builder.py. This project's models don't use SQLAlchemy
relationships anywhere (see repos.py's own plain select() pattern) — sections
and citations are fetched with their own queries and assembled here.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.auth import get_current_user
from app.audio.dependencies import get_speech_provider
from app.audio.providers import SpeechProvider
from app.audio.speech_response import stream_speech
from app.config import settings
from app.db.models import (
    AnalysisSnapshot,
    Citation,
    PatternClaim,
    Repo,
    Section,
    StudyGuide,
    Subsystem,
    TradeoffCard,
    User,
)
from app.db.session import get_session
from app.generation.diffing import (
    DependencyDiff,
    PatternDiff,
    SubsystemDiff,
    TradeoffDiff,
    diff_dependencies,
    diff_pattern,
    diff_subsystems,
    diff_tradeoffs,
)

router = APIRouter(prefix="/study-guides", tags=["study-guides"])


class CitationOut(BaseModel):
    id: UUID
    file_path: str
    line_start: int
    line_end: int
    claim_excerpt: str
    snippet_text: str


class SectionOut(BaseModel):
    id: UUID
    section_type: str
    title: str
    order: int
    content_md: str
    diagram_mermaid: str | None
    citations: list[CitationOut]


class StudyGuideOut(BaseModel):
    id: UUID
    repo_id: UUID
    snapshot_id: UUID
    version: int
    # RepoDetail.tsx's "generated N hours ago" line. The snapshot's indexed_at
    # is the closest already-exposed timestamp, but it dates the *analysis*,
    # not the writing pass that ran after it — and a re-run of generation over
    # an unchanged snapshot would leave it stale.
    generated_at: datetime
    sections: list[SectionOut]


async def _owned_guide_and_repo(
    session: AsyncSession, study_guide_id: UUID, current_user: User
) -> tuple[StudyGuide, Repo]:
    guide = await session.get(StudyGuide, study_guide_id)
    if guide is None:
        raise HTTPException(404, "study guide not found")

    # Same "404, not 403" reasoning as api/repos.py's ownership checks — a
    # guide's repo_id is enough to scope it without a join.
    repo = await session.get(Repo, guide.repo_id)
    if repo is None or repo.user_id != current_user.id:
        raise HTTPException(404, "study guide not found")
    return guide, repo


async def _sections_with_citations(
    session: AsyncSession, guide: StudyGuide
) -> tuple[list[Section], dict[UUID, list[Citation]]]:
    sections = list(
        (
            await session.exec(select(Section).where(Section.study_guide_id == guide.id).order_by(Section.order))
        ).all()
    )
    section_ids = [s.id for s in sections]
    citations_by_section: dict[UUID, list[Citation]] = {sid: [] for sid in section_ids}
    if section_ids:
        citations = (await session.exec(select(Citation).where(Citation.section_id.in_(section_ids)))).all()
        for cite in citations:
            citations_by_section[cite.section_id].append(cite)
    return sections, citations_by_section


def _safe_filename_stem(display_name: str) -> str:
    """A repo's display_name is user-supplied (a git URL, or an uploaded zip's
    own filename) and goes into a Content-Disposition header — anything that
    isn't a plain word character becomes a hyphen so it can't inject header
    syntax or a path separator."""
    stem = "".join(ch if ch.isalnum() else "-" for ch in display_name).strip("-")
    while "--" in stem:
        stem = stem.replace("--", "-")
    return stem.lower()[:60] or "study-guide"


def _render_markdown(guide: StudyGuide, repo: Repo, snapshot_commit: str | None, sections, citations_by_section) -> str:
    lines = [f"# {repo.display_name} — study guide", "", f"Version {guide.version}."]
    if snapshot_commit:
        lines.append(f"Indexed at commit `{snapshot_commit}`.")
    lines.append("")

    for section in sections:
        lines += [f"## {section.title}", "", section.content_md, ""]
        if section.diagram_mermaid:
            # A fence, not an image: Mermaid is text by design (ADR-004), and
            # GitHub and most Markdown viewers render this fence natively.
            lines += ["```mermaid", section.diagram_mermaid, "```", ""]
        citations = citations_by_section.get(section.id, [])
        if citations:
            lines += ["**Citations**", ""]
            for cite in sorted(citations, key=lambda c: (c.file_path, c.line_start)):
                lines.append(f"- `{cite.file_path}:{cite.line_start}-{cite.line_end}` — {cite.claim_excerpt}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


class StudyGuideDiffOut(BaseModel):
    from_version: int
    to_version: int
    from_snapshot_id: UUID
    to_snapshot_id: UUID
    from_commit: str | None
    to_commit: str | None
    subsystems: SubsystemDiff
    tradeoffs: TradeoffDiff
    pattern: PatternDiff
    dependencies: DependencyDiff


async def _snapshot_facts(
    session: AsyncSession, snapshot_id: UUID
) -> tuple[list[Subsystem], list[TradeoffCard], PatternClaim | None]:
    subsystems = list((await session.exec(select(Subsystem).where(Subsystem.snapshot_id == snapshot_id))).all())
    cards = list((await session.exec(select(TradeoffCard).where(TradeoffCard.snapshot_id == snapshot_id))).all())
    pattern = (await session.exec(select(PatternClaim).where(PatternClaim.snapshot_id == snapshot_id))).first()
    return subsystems, cards, pattern


@router.get("/{study_guide_id}/diff/{other_study_guide_id}", response_model=StudyGuideDiffOut)
async def diff_study_guides(
    study_guide_id: UUID,
    other_study_guide_id: UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> StudyGuideDiffOut:
    """What changed between two indexings of the same repository.

    Direction is by version, not by argument order: the lower-versioned guide
    is always "before". A diff that reverses itself depending on which id the
    caller happened to put first would be a trap, and there's no case where
    reading the repository's history backwards is what someone meant.

    Structure only, no prose summary — see generation/diffing.py for why
    nothing here matches on generated text.
    """
    guide_a, repo = await _owned_guide_and_repo(session, study_guide_id, current_user)
    guide_b, other_repo = await _owned_guide_and_repo(session, other_study_guide_id, current_user)

    if repo.id != other_repo.id:
        # Not an empty diff: comparing two repositories' architectures is a
        # different question this endpoint doesn't answer, and silently
        # returning "everything changed" would look like an answer.
        raise HTTPException(400, "study guides belong to different repositories")

    before, after = sorted((guide_a, guide_b), key=lambda g: g.version)

    snapshot_before = await session.get(AnalysisSnapshot, before.snapshot_id)
    snapshot_after = await session.get(AnalysisSnapshot, after.snapshot_id)
    if snapshot_before is None or snapshot_after is None:
        raise HTTPException(404, "snapshot not found")

    subsystems_before, cards_before, pattern_before = await _snapshot_facts(session, before.snapshot_id)
    subsystems_after, cards_after, pattern_after = await _snapshot_facts(session, after.snapshot_id)

    return StudyGuideDiffOut(
        from_version=before.version,
        to_version=after.version,
        from_snapshot_id=before.snapshot_id,
        to_snapshot_id=after.snapshot_id,
        from_commit=snapshot_before.commit_hash,
        to_commit=snapshot_after.commit_hash,
        subsystems=diff_subsystems(subsystems_before, subsystems_after),
        tradeoffs=diff_tradeoffs(cards_before, cards_after),
        pattern=diff_pattern(pattern_before, pattern_after),
        dependencies=diff_dependencies(
            snapshot_before.dependency_graph, subsystems_before, snapshot_after.dependency_graph, subsystems_after
        ),
    )


@router.get("/{study_guide_id}/sections/{section_id}/speech")
async def speak_section(
    study_guide_id: UUID,
    section_id: UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
    speaker: SpeechProvider | None = Depends(get_speech_provider),
) -> StreamingResponse:
    """Read-aloud for one persisted section (ADR-010). Identifier-based
    only — the text spoken is Section.content_md, never caller-supplied, so
    this is not a paid text-to-speech proxy. Ownership goes through the
    guide's repo like every other route here; the section must belong to
    *that* guide, not merely exist.

    See app/audio/speech_response.py for the streaming shape and why the
    first chunk is awaited before the response starts.
    """
    guide, _repo = await _owned_guide_and_repo(session, study_guide_id, current_user)
    section = await session.get(Section, section_id)
    if section is None or section.study_guide_id != guide.id:
        raise HTTPException(404, "section not found")
    return await stream_speech(speaker, section.content_md, max_chars=settings.speech_max_chars)


@router.get("/{study_guide_id}/export.md")
async def export_study_guide_markdown(
    study_guide_id: UUID, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)
) -> PlainTextResponse:
    """Markdown only — PDF is deliberately out of scope (#66): it needs a real
    rendering dependency to produce what the browser's own print-to-PDF already
    does from the rendered page.

    Assembly rather than rendering: `content_md` is already Markdown, the
    diagram is already a Mermaid text block (ADR-004), and citations are
    structured rows.
    """
    guide, repo = await _owned_guide_and_repo(session, study_guide_id, current_user)
    snapshot = await session.get(AnalysisSnapshot, guide.snapshot_id)
    sections, citations_by_section = await _sections_with_citations(session, guide)

    body = _render_markdown(
        guide, repo, snapshot.commit_hash if snapshot is not None else None, sections, citations_by_section
    )
    filename = f"{_safe_filename_stem(repo.display_name)}-v{guide.version}.md"
    return PlainTextResponse(
        body,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{study_guide_id}", response_model=StudyGuideOut)
async def get_study_guide(
    study_guide_id: UUID, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)
) -> StudyGuideOut:
    guide, _repo = await _owned_guide_and_repo(session, study_guide_id, current_user)
    sections, citations_by_section = await _sections_with_citations(session, guide)

    return StudyGuideOut(
        id=guide.id,
        repo_id=guide.repo_id,
        snapshot_id=guide.snapshot_id,
        version=guide.version,
        generated_at=guide.generated_at,
        sections=[
            SectionOut(
                id=section.id,
                section_type=section.section_type.value,
                title=section.title,
                order=section.order,
                content_md=section.content_md,
                diagram_mermaid=section.diagram_mermaid,
                citations=[
                    CitationOut(
                        id=c.id,
                        file_path=c.file_path,
                        line_start=c.line_start,
                        line_end=c.line_end,
                        claim_excerpt=c.claim_excerpt,
                        snippet_text=c.snippet_text,
                    )
                    for c in citations_by_section[section.id]
                ],
            )
            for section in sections
        ],
    )
