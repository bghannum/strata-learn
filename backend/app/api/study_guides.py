"""GET /study-guides/{id}: the assembled study guide with its ordered
sections and each section's citations — the read side of
generation/study_guide_builder.py. This project's models don't use SQLAlchemy
relationships anywhere (see repos.py's own plain select() pattern) — sections
and citations are fetched with their own queries and assembled here.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.auth import get_current_user
from app.db.models import AnalysisSnapshot, Citation, Repo, Section, StudyGuide, User
from app.db.session import get_session

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
