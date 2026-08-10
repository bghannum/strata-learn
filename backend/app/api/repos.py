"""POST /repos runs the Phase 1 pipeline synchronously — clone/extract, walk,
parse, build the dependency graph, persist. No job queue yet (D13); that
refactor lands in Phase 1.5 once this pipeline is proven correct end-to-end.
"""

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.analysis.snapshot import analyze_source, persist_snapshot
from app.db.models import AnalysisSnapshot, Repo, SourceType
from app.db.session import get_session
from app.ingestion.source import (
    SourcePreparationError,
    cleanup_workspace,
    clone_git_repo,
    extract_zip_upload,
)

router = APIRouter(prefix="/repos", tags=["repos"])


@router.post("", response_model=Repo, status_code=201)
async def create_repo(
    source_type: SourceType = Form(...),
    git_url: str | None = Form(None),
    display_name: str | None = Form(None),
    file: UploadFile | None = File(None),
    session: AsyncSession = Depends(get_session),
) -> Repo:
    if source_type == SourceType.git_url:
        if not git_url:
            raise HTTPException(422, "git_url is required when source_type is git_url")
        source_uri = git_url
    else:
        if file is None or not file.filename:
            raise HTTPException(422, "file is required when source_type is zip_upload")
        source_uri = file.filename

    job_id = uuid4()
    try:
        if source_type == SourceType.git_url:
            source_dir, commit_hash = clone_git_repo(git_url, job_id)  # type: ignore[arg-type]
        else:
            source_dir = extract_zip_upload(file.file, job_id)  # type: ignore[union-attr]
            commit_hash = None
        analysis = analyze_source(source_dir)
    except SourcePreparationError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        cleanup_workspace(job_id)

    repo = Repo(
        source_type=source_type,
        source_uri=source_uri,
        display_name=display_name or source_uri,
    )
    session.add(repo)
    await session.flush()  # assigns repo.id, needed for AnalysisSnapshot.repo_id below

    # persist_snapshot commits — this lands repo + snapshot + code units in one
    # transaction, so a mid-pipeline failure never leaves a repo with no snapshot.
    snapshot = await persist_snapshot(session, repo.id, commit_hash, analysis)

    repo.latest_snapshot_id = snapshot.id
    session.add(repo)
    await session.commit()
    await session.refresh(repo)

    return repo


@router.get("", response_model=list[Repo])
async def list_repos(session: AsyncSession = Depends(get_session)) -> list[Repo]:
    result = await session.exec(select(Repo).order_by(Repo.created_at.desc()))
    return list(result.all())


@router.get("/{repo_id}", response_model=Repo)
async def get_repo(repo_id: UUID, session: AsyncSession = Depends(get_session)) -> Repo:
    repo = await session.get(Repo, repo_id)
    if repo is None:
        raise HTTPException(404, "repo not found")
    return repo


@router.get("/{repo_id}/snapshot", response_model=AnalysisSnapshot)
async def get_latest_snapshot(repo_id: UUID, session: AsyncSession = Depends(get_session)) -> AnalysisSnapshot:
    repo = await session.get(Repo, repo_id)
    if repo is None:
        raise HTTPException(404, "repo not found")
    if repo.latest_snapshot_id is None:
        raise HTTPException(404, "repo has no snapshot yet")
    snapshot = await session.get(AnalysisSnapshot, repo.latest_snapshot_id)
    if snapshot is None:
        raise HTTPException(404, "snapshot not found")
    return snapshot
