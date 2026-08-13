"""GET /study-guides/{id}: the assembled study guide with its ordered
sections and each section's citations — the read side of
generation/study_guide_builder.py. This project's models don't use SQLAlchemy
relationships anywhere (see repos.py's own plain select() pattern) — sections
and citations are fetched with their own queries and assembled here.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.auth import get_current_user
from app.db.models import Citation, Repo, Section, StudyGuide, User
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


@router.get("/{study_guide_id}", response_model=StudyGuideOut)
async def get_study_guide(
    study_guide_id: UUID, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)
) -> StudyGuideOut:
    guide = await session.get(StudyGuide, study_guide_id)
    if guide is None:
        raise HTTPException(404, "study guide not found")

    # Same "404, not 403" reasoning as api/repos.py's ownership checks — a
    # guide's repo_id is enough to scope it without a join.
    repo = await session.get(Repo, guide.repo_id)
    if repo is None or repo.user_id != current_user.id:
        raise HTTPException(404, "study guide not found")

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
