"""POST /repos creates the Repo + a pending AnalysisSnapshot synchronously, then
enqueues the indexing pipeline as an arq job and returns immediately (Phase 1.5,
D13/ADR-002) — worker/pipeline.py does the actual clone/extract/analyze/persist
work that this endpoint ran inline through Phase 1.

Obviously-bad input (unreachable git URL, malformed/oversized zip) still 422s
synchronously via a cheap pre-check (git ls-remote / zip central-directory read)
— see ingestion/source.py. Failures that can only be discovered mid-clone or
mid-extract still surface async, as `status=failed` over WS /repos/{id}/progress.
"""

import asyncio
import io
import json
from uuid import UUID

from arq.connections import ArqRedis
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.analysis.snapshot import create_pending_snapshot, fail_snapshot
from app.config import settings
from app.db.models import AnalysisSnapshot, Repo, SnapshotStatus, SourceType
from app.db.session import get_session
from app.ingestion.source import (
    SourcePreparationError,
    check_git_url_reachable,
    validate_zip_upload,
)
from app.redis_pool import get_redis_pool
from app.worker.pipeline import progress_channel

router = APIRouter(prefix="/repos", tags=["repos"])

# Safety net only — worker/pipeline.py deletes the key itself once the job
# consumes it (success or failure). This just bounds how long an upload can
# linger in Redis if a job is enqueued but never runs (e.g. worker crash).
ZIP_UPLOAD_TTL_SECONDS = 3600


@router.post("", response_model=Repo, status_code=201)
async def create_repo(
    source_type: SourceType = Form(...),
    git_url: str | None = Form(None),
    display_name: str | None = Form(None),
    file: UploadFile | None = File(None),
    session: AsyncSession = Depends(get_session),
    redis: ArqRedis = Depends(get_redis_pool),
) -> Repo:
    zip_bytes: bytes | None = None

    if source_type == SourceType.git_url:
        if not git_url:
            raise HTTPException(422, "git_url is required when source_type is git_url")
        source_uri = git_url
        try:
            # git ls-remote shells out and blocks on network I/O — run it off
            # the event loop so one slow/unresponsive remote can't stall every
            # other concurrent request (including other clients' progress
            # websockets) for its duration. The timeout itself is enforced
            # inside check_git_url_reachable via kill_after_timeout, which
            # actually kills the git subprocess — an asyncio-level timeout
            # here alone couldn't do that, only stop waiting on it.
            await asyncio.to_thread(check_git_url_reachable, git_url)
        except SourcePreparationError as exc:
            raise HTTPException(422, str(exc)) from exc
    else:
        if file is None or not file.filename:
            raise HTTPException(422, "file is required when source_type is zip_upload")
        source_uri = file.filename
        # Bounded read, not file.read() — an unbounded read buffers the whole
        # upload into memory before validate_zip_upload's size check ever
        # runs. Reading max_bytes+1 caps worst-case memory to that regardless
        # of how large the actual upload is.
        zip_bytes = await file.read(settings.zip_upload_max_bytes + 1)
        if len(zip_bytes) > settings.zip_upload_max_bytes:
            raise HTTPException(422, f"Upload exceeds the {settings.zip_upload_max_bytes}-byte limit")
        try:
            validate_zip_upload(io.BytesIO(zip_bytes))
        except SourcePreparationError as exc:
            raise HTTPException(422, str(exc)) from exc

    repo = Repo(
        source_type=source_type,
        source_uri=source_uri,
        display_name=display_name or source_uri,
    )
    session.add(repo)
    await session.flush()  # assigns repo.id, needed for AnalysisSnapshot.repo_id below

    snapshot = await create_pending_snapshot(session, repo.id)
    repo.latest_snapshot_id = snapshot.id
    session.add(repo)
    await session.commit()
    await session.refresh(repo)

    zip_redis_key: str | None = None
    try:
        if zip_bytes is not None:
            zip_redis_key = f"zip-upload:{snapshot.id}"
            await redis.set(zip_redis_key, zip_bytes, ex=ZIP_UPLOAD_TTL_SECONDS)

        await redis.enqueue_job(
            "index_repo",
            snapshot_id=snapshot.id,
            repo_id=repo.id,
            source_type=source_type.value,
            git_url=git_url,
            zip_redis_key=zip_redis_key,
        )
    except Exception as exc:
        # repo + pending snapshot are already committed above — left alone, a
        # Redis failure here would strand them at "pending" forever (no job
        # ever gets enqueued to move them forward) while the client sees a
        # bare 500 suggesting nothing was created at all.
        await fail_snapshot(session, snapshot.id)
        raise HTTPException(503, "Could not queue indexing job — try again shortly") from exc

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


@router.websocket("/{repo_id}/progress")
async def repo_progress(
    repo_id: UUID,
    websocket: WebSocket,
    session: AsyncSession = Depends(get_session),
    redis: ArqRedis = Depends(get_redis_pool),
) -> None:
    await websocket.accept()

    repo = await session.get(Repo, repo_id)
    if repo is None or repo.latest_snapshot_id is None:
        await websocket.close(code=4404)
        return

    pubsub = redis.pubsub()
    await pubsub.subscribe(progress_channel(repo.latest_snapshot_id))
    try:
        # Re-fetch *after* subscribing, not before — otherwise a status
        # transition landing in the gap between an earlier read and
        # subscribing would never reach this client (a duplicate status
        # message here is harmless; a missed one isn't).
        snapshot = await session.get(AnalysisSnapshot, repo.latest_snapshot_id)
        if snapshot is not None:
            await websocket.send_json({"status": snapshot.status.value})
            if snapshot.status in (SnapshotStatus.ready, SnapshotStatus.failed):
                return  # already terminal — worker will never publish again

        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            payload = json.loads(message["data"])
            await websocket.send_json(payload)
            if payload["status"] in (SnapshotStatus.ready.value, SnapshotStatus.failed.value):
                break
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe()
        await pubsub.aclose()
