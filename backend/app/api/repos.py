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
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from arq.connections import ArqRedis
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.analysis.snapshot import create_pending_snapshot, fail_snapshot
from app.api.auth import get_current_user
from app.auth.session import get_user_from_token
from app.config import settings
from app.db.models import (
    AnalysisSnapshot,
    AnswerSubmission,
    Attempt,
    AttemptStatus,
    Question,
    Quiz,
    Repo,
    SnapshotStatus,
    SourceType,
    StudyGuide,
    Subsystem,
    User,
)
from app.db.session import get_session
from app.ingestion.source import (
    SourcePreparationError,
    check_git_url_reachable,
    get_remote_head_commit,
    validate_zip_upload,
)
from app.quizzing.mastery import CountedAttempt, GradedAnswer, compute_mastery, select_counted_attempts
from app.redis_pool import get_redis_pool
from app.worker.pipeline import progress_channel

router = APIRouter(prefix="/repos", tags=["repos"])

# Sole cleanup mechanism for zip uploads (worker/pipeline.py deliberately
# never deletes this key itself — see its module docstring). Wide on purpose:
# the job needs the key to still exist whenever it actually runs, including
# after a redelivery, and a worker that's down or badly backlogged for longer
# than a short TTL would otherwise deterministically fail an otherwise-valid
# upload through no fault of its own. 24h comfortably covers a worker outage
# on a solo local setup without the key lingering indefinitely if a job is
# enqueued but genuinely never runs.
ZIP_UPLOAD_TTL_SECONDS = 24 * 60 * 60

# How long the progress WebSocket waits for a pub/sub message before re-reading
# the snapshot row directly (#9). Redis pub/sub has no delivery guarantee, so
# the database — not the message — is the source of truth about whether
# indexing finished; this bounds how long a dropped terminal message can leave
# a client hanging. Short enough that a missed "ready" is a blink rather than a
# stuck page, long enough that an idle client isn't polling Postgres hard.
PROGRESS_POLL_FALLBACK_SECONDS = 5.0


@router.post("", response_model=Repo, status_code=201)
async def create_repo(
    source_type: SourceType = Form(...),
    git_url: str | None = Form(None),
    display_name: str | None = Form(None),
    file: UploadFile | None = File(None),
    session: AsyncSession = Depends(get_session),
    redis: ArqRedis = Depends(get_redis_pool),
    current_user: User = Depends(get_current_user),
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
        user_id=current_user.id,
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


class ReindexRequest(BaseModel):
    # Re-running the pipeline costs real Layer B money, so an unchanged repo is
    # refused by default and this is the explicit override (#73).
    force: bool = False


@router.post("/{repo_id}/reindex", response_model=Repo, status_code=202)
async def reindex_repo(
    repo_id: UUID,
    body: ReindexRequest | None = None,
    session: AsyncSession = Depends(get_session),
    redis: ArqRedis = Depends(get_redis_pool),
    current_user: User = Depends(get_current_user),
) -> Repo:
    """Re-runs the full pipeline against a fresh AnalysisSnapshot.

    Two user intents, one mechanism: retrying a `failed` run (ui-spec.md §6.2,
    #26) and re-indexing a `ready` repo to pick up new commits (#73). Both are
    a run from scratch rather than a resume — Layer B's per-module/pattern/
    trade-off work isn't persisted incrementally in a form safe to resume
    mid-run.

    Re-indexing a healthy repo was originally out of scope, which left the
    versioning story with its middle step missing: staleness detection could
    say "new commits on the remote" and offer no way to act, and the
    architectural diff (#63) had no way to ever acquire a second snapshot to
    compare.

    Eligibility, in order:

    - a non-terminal snapshot (pending/parsing/analyzing/generating) is a job
      already in flight — a second one would race it for the same
      latest_snapshot_id and double the billed work;
    - a `failed` snapshot is always eligible, unchanged from #26;
    - a `ready` snapshot is eligible only when the remote has actually moved,
      or `force` is set. Re-indexing identical source spends the most expensive
      part of the pipeline to regenerate what already exists, which is a
      reasonable thing to ask for deliberately and a bad thing to do by
      double-click.
    """
    force = body.force if body is not None else False

    # SELECT ... FOR UPDATE, not session.get — found via Codex's PR #50
    # review: without a lock, two concurrent retries can both read the same
    # failed snapshot before either updates latest_snapshot_id, so both
    # create and enqueue a fresh snapshot (two full pipelines, including
    # paid Layer B calls, with one snapshot silently orphaned). This
    # serializes concurrent reindex calls on the same repo row — same
    # pattern as attempts.py's _owned_attempt_for_update.
    repo = (await session.exec(select(Repo).where(Repo.id == repo_id).with_for_update())).first()
    if repo is None or repo.user_id != current_user.id:
        raise HTTPException(404, "repo not found")
    if repo.latest_snapshot_id is None:
        raise HTTPException(404, "repo has no snapshot yet")

    old_snapshot = await session.get(AnalysisSnapshot, repo.latest_snapshot_id)
    if old_snapshot is None:
        raise HTTPException(404, "snapshot not found")
    if old_snapshot.status not in (SnapshotStatus.ready, SnapshotStatus.failed):
        raise HTTPException(409, "indexing is already in progress for this repository")

    if old_snapshot.status == SnapshotStatus.ready and not force:
        if repo.source_type != SourceType.git_url:
            # A zip re-index would analyze the exact same bytes — there is no
            # remote it could have drifted from, so "has anything changed?"
            # has one honest answer.
            raise HTTPException(409, "an uploaded zip has no remote to pick up changes from — pass force to re-index")

        # A *fresh* check rather than the cached remote_head_commit: this is
        # the request that spends the money, so it shouldn't act on an answer
        # that may be hours old. Recording it also keeps update-status honest.
        remote_head = await asyncio.to_thread(get_remote_head_commit, repo.source_uri)
        repo.remote_head_commit = remote_head
        repo.updates_checked_at = datetime.now(UTC)
        session.add(repo)
        await session.commit()

        # An unreachable remote can't prove the repo is unchanged. Refusing on
        # a network hiccup would block a legitimate action for the wrong
        # reason, so an unknown answer allows the re-index.
        if remote_head is not None and remote_head == old_snapshot.commit_hash:
            raise HTTPException(
                409, "already indexed at the remote's current commit — pass force to re-index anyway"
            )

    zip_bytes: bytes | None = None
    if repo.source_type == SourceType.zip_upload:
        # The original upload's bytes are keyed by the *old* snapshot's id
        # with a 24h TTL (see ZIP_UPLOAD_TTL_SECONDS) — checked before
        # creating anything new, so an expired upload fails fast rather than
        # leaving a fresh pending snapshot that can never actually run.
        zip_bytes = await redis.get(f"zip-upload:{old_snapshot.id}")
        if zip_bytes is None:
            raise HTTPException(
                410,
                "The uploaded zip is no longer available — add this repository again with a fresh upload.",
            )

    new_snapshot = await create_pending_snapshot(session, repo.id)
    repo.latest_snapshot_id = new_snapshot.id
    session.add(repo)
    await session.commit()
    await session.refresh(repo)

    zip_redis_key: str | None = None
    try:
        if zip_bytes is not None:
            zip_redis_key = f"zip-upload:{new_snapshot.id}"
            await redis.set(zip_redis_key, zip_bytes, ex=ZIP_UPLOAD_TTL_SECONDS)

        await redis.enqueue_job(
            "index_repo",
            snapshot_id=new_snapshot.id,
            repo_id=repo.id,
            source_type=repo.source_type.value,
            git_url=repo.source_uri if repo.source_type == SourceType.git_url else None,
            zip_redis_key=zip_redis_key,
        )
    except Exception as exc:
        # Same reasoning as create_repo's own enqueue-failure handling above:
        # the new snapshot is already committed, so it must be explicitly
        # marked failed rather than left stranded at "pending" forever.
        await fail_snapshot(session, new_snapshot.id)
        raise HTTPException(503, "Could not queue indexing job — try again shortly") from exc

    return repo


@router.get("", response_model=list[Repo])
async def list_repos(
    session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)
) -> list[Repo]:
    result = await session.exec(
        select(Repo).where(Repo.user_id == current_user.id).order_by(Repo.created_at.desc())
    )
    return list(result.all())


@router.get("/{repo_id}", response_model=Repo)
async def get_repo(
    repo_id: UUID, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)
) -> Repo:
    repo = await session.get(Repo, repo_id)
    # 404, not 403, for a repo that exists but belongs to someone else — a
    # distinct "forbidden" response would confirm the repo_id is real to a
    # caller who shouldn't be able to tell either way.
    if repo is None or repo.user_id != current_user.id:
        raise HTTPException(404, "repo not found")
    return repo


class UpdateStatusOut(BaseModel):
    """`status` is derived on every read rather than stored — see Repo's own
    comment on why persisting a verdict would go silently wrong after a
    reindex."""

    status: Literal["up_to_date", "stale", "unknown"]
    checked_at: datetime | None
    remote_commit: str | None
    indexed_commit: str | None
    # Distinguishes the several ways "unknown" happens, so the UI can say
    # "zip uploads can't be checked" instead of an unexplained shrug.
    reason: str | None = None


async def _update_status(session: AsyncSession, repo: Repo) -> UpdateStatusOut:
    indexed_commit: str | None = None
    if repo.latest_snapshot_id is not None:
        snapshot = await session.get(AnalysisSnapshot, repo.latest_snapshot_id)
        indexed_commit = snapshot.commit_hash if snapshot is not None else None

    common = {
        "checked_at": repo.updates_checked_at,
        "remote_commit": repo.remote_head_commit,
        "indexed_commit": indexed_commit,
    }

    if repo.source_type != SourceType.git_url:
        # A zip upload has no remote to compare against and no commit_hash of
        # its own (see the column's comment) — permanently uncheckable, which
        # is a real answer rather than a failure.
        return UpdateStatusOut(status="unknown", reason="zip_upload", **common)
    if repo.updates_checked_at is None:
        return UpdateStatusOut(status="unknown", reason="never_checked", **common)
    if repo.remote_head_commit is None:
        return UpdateStatusOut(status="unknown", reason="remote_unreachable", **common)
    if indexed_commit is None:
        return UpdateStatusOut(status="unknown", reason="no_indexed_commit", **common)
    status = "up_to_date" if indexed_commit == repo.remote_head_commit else "stale"
    return UpdateStatusOut(status=status, **common)


@router.get("/{repo_id}/update-status", response_model=UpdateStatusOut)
async def get_update_status(
    repo_id: UUID, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)
) -> UpdateStatusOut:
    """Reads the last check's result. Deliberately does no network I/O — a
    `git ls-remote` on a page-load path would make every repo view wait on a
    third-party host that can hang (#62). Checking is an explicit action; see
    the POST below."""
    repo = await session.get(Repo, repo_id)
    if repo is None or repo.user_id != current_user.id:
        raise HTTPException(404, "repo not found")
    return await _update_status(session, repo)


@router.post("/{repo_id}/check-updates", response_model=UpdateStatusOut)
async def check_updates(
    repo_id: UUID, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)
) -> UpdateStatusOut:
    """Asks the remote for its current HEAD and records the answer.

    The one place network I/O happens for this feature, and only because a
    user asked for it. Offloaded to a thread for the same reason register()
    offloads bcrypt: `git ls-remote` is blocking, and the single Uvicorn event
    loop also serves the repo-progress WebSocket that other requests may be
    waiting on.

    An unreachable remote is recorded as "checked, couldn't tell" rather than
    raising — the repository and its guide are both still fine, and a 5xx
    would misrepresent a network hiccup as a broken request.
    """
    repo = await session.get(Repo, repo_id)
    if repo is None or repo.user_id != current_user.id:
        raise HTTPException(404, "repo not found")
    if repo.source_type != SourceType.git_url:
        raise HTTPException(409, "only a git-backed repo can be checked for updates")

    repo.remote_head_commit = await asyncio.to_thread(get_remote_head_commit, repo.source_uri)
    repo.updates_checked_at = datetime.now(UTC)
    session.add(repo)
    await session.commit()
    await session.refresh(repo)
    return await _update_status(session, repo)


class MasteryPointOut(BaseModel):
    completed_at: datetime
    answered: int
    average_score: float


class MasteryBucketOut(BaseModel):
    subsystem_key: str
    name: str
    attempts: int
    answered: int
    average_score: float
    history: list[MasteryPointOut]


class MasteryOut(BaseModel):
    # Distinguishes "no quizzes taken yet" from "quizzes taken, nothing to
    # aggregate" — the first is a prompt to go take one, the second would be a
    # bug worth noticing.
    completed_attempts: int
    buckets: list[MasteryBucketOut]


@router.get("/{repo_id}/mastery", response_model=MasteryOut)
async def get_repo_mastery(
    repo_id: UUID, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)
) -> MasteryOut:
    """Quiz performance per subsystem, across every study-guide version of this
    repo (#64).

    Only *completed* attempts count: an abandoned in_progress attempt is not
    evidence of anything, and counting its partial answers would drag an
    average down for a quiz the learner never actually finished.
    """
    repo = await session.get(Repo, repo_id)
    if repo is None or repo.user_id != current_user.id:
        raise HTTPException(404, "repo not found")

    quiz_ids = list((await session.exec(select(Quiz.id).where(Quiz.repo_id == repo_id))).all())
    if not quiz_ids:
        return MasteryOut(completed_attempts=0, buckets=[])

    attempts = list(
        (
            await session.exec(
                select(Attempt).where(
                    Attempt.quiz_id.in_(quiz_ids),
                    Attempt.user_id == current_user.id,
                    Attempt.status == AttemptStatus.completed,
                )
            )
        ).all()
    )
    attempts = [a for a in attempts if a.completed_at is not None]
    if not attempts:
        return MasteryOut(completed_attempts=0, buckets=[])

    rows = list(
        (
            await session.exec(
                select(AnswerSubmission.attempt_id, Question.subsystem_key, AnswerSubmission.score)
                .join(Question, Question.id == AnswerSubmission.question_id)
                .where(AnswerSubmission.attempt_id.in_([a.id for a in attempts]))
            )
        ).all()
    )
    answers_by_attempt: dict[UUID, list[GradedAnswer]] = {}
    for attempt_id, subsystem_key, score in rows:
        if score is None:
            # Only possible in the instant between insert and grading within one
            # request (see AnswerSubmission.score) — not a graded result.
            continue
        answers_by_attempt.setdefault(attempt_id, []).append(
            GradedAnswer(subsystem_key=subsystem_key, score=score)
        )

    counted = [
        CountedAttempt(quiz_id=a.quiz_id, completed_at=a.completed_at, answers=answers_by_attempt.get(a.id, []))
        for a in attempts
    ]

    # Names come from the latest snapshot: keys are the join identity, names are
    # what a person reads, and the current name is the one that matches what
    # they'd see elsewhere in the app.
    subsystem_names: dict[str, str] = {}
    if repo.latest_snapshot_id is not None:
        subsystems = list(
            (await session.exec(select(Subsystem).where(Subsystem.snapshot_id == repo.latest_snapshot_id))).all()
        )
        subsystem_names = {s.key: s.name for s in subsystems}

    buckets = compute_mastery(counted, subsystem_names)
    return MasteryOut(
        completed_attempts=len(select_counted_attempts(counted)),
        buckets=[
            MasteryBucketOut(
                subsystem_key=b.subsystem_key,
                name=b.name,
                attempts=b.attempts,
                answered=b.answered,
                average_score=b.average_score,
                history=[
                    MasteryPointOut(completed_at=p.completed_at, answered=p.answered, average_score=p.average_score)
                    for p in b.history
                ],
            )
            for b in buckets
        ],
    )


class AttemptSummaryOut(BaseModel):
    id: UUID
    quiz_id: UUID
    completed_at: datetime
    score: float
    question_count: int


class AttemptHistoryOut(BaseModel):
    """An envelope rather than a bare list (#75): once the response is a page,
    the panel has to be able to say *of how many* — "showing 10 of 23" is the
    difference between a bounded list and a list that looks complete but
    silently isn't."""

    items: list[AttemptSummaryOut]
    total: int


# Retakes are unlimited, so both this response and the DOM that renders it grow
# without a ceiling unless something imposes one (#75). Ten is a sitting's worth
# of recent history — enough to see a trend, small enough to read — and the
# ceiling bounds what "show all" can ask for, since an unbounded escape hatch
# would just move the same problem behind a click.
DEFAULT_ATTEMPT_PAGE_SIZE = 10
MAX_ATTEMPT_PAGE_SIZE = 100


@router.get("/{repo_id}/attempts", response_model=AttemptHistoryOut)
async def list_repo_attempts(
    repo_id: UUID,
    limit: int = Query(DEFAULT_ATTEMPT_PAGE_SIZE, ge=1, le=MAX_ATTEMPT_PAGE_SIZE),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AttemptHistoryOut:
    """Quiz history for RepoDetail.tsx: the most recent completed attempts over
    any quiz for this repo, newest first, with the full count alongside.

    Deliberately not the same data as /mastery, which aggregates *answers* by
    subsystem across attempts and so can't say "you scored 80% on a 5-question
    quiz on Tuesday". This is the per-sitting record; that one is the per-topic
    one.

    Completed attempts only, for the same reason mastery excludes in-progress
    ones — an abandoned attempt has no score to report, and listing it as
    history would misrepresent a quiz the learner never finished. Resuming one
    is already handled by POST /attempts.
    """
    repo = await session.get(Repo, repo_id)
    if repo is None or repo.user_id != current_user.id:
        raise HTTPException(404, "repo not found")

    quiz_ids = list((await session.exec(select(Quiz.id).where(Quiz.repo_id == repo_id))).all())
    if not quiz_ids:
        return AttemptHistoryOut(items=[], total=0)

    completed = (
        Attempt.quiz_id.in_(quiz_ids),
        Attempt.user_id == current_user.id,
        Attempt.status == AttemptStatus.completed,
    )
    # Counted separately rather than derived from the page: the whole point of
    # `total` is to describe the rows this response *didn't* return.
    total = (await session.exec(select(func.count(Attempt.id)).where(*completed))).one()

    attempts = list(
        (
            await session.exec(
                select(Attempt).where(*completed).order_by(Attempt.completed_at.desc()).limit(limit)
            )
        ).all()
    )
    if not attempts:
        return AttemptHistoryOut(items=[], total=total)

    # One grouped count for every quiz involved rather than a query per row —
    # a repo re-indexed a few times can easily have several quizzes, and the
    # count is a property of the quiz, not of the attempt.
    counts = dict(
        (
            await session.exec(
                select(Question.quiz_id, func.count(Question.id))
                .where(Question.quiz_id.in_({a.quiz_id for a in attempts}))
                .group_by(Question.quiz_id)
            )
        ).all()
    )

    return AttemptHistoryOut(
        items=[
            AttemptSummaryOut(
                id=a.id,
                quiz_id=a.quiz_id,
                completed_at=a.completed_at,
                # Both are set together on completion (api/attempts.py), so a
                # completed attempt missing either is a bug, not a state to render.
                score=a.score if a.score is not None else 0.0,
                question_count=counts.get(a.quiz_id, 0),
            )
            for a in attempts
            if a.completed_at is not None
        ],
        total=total,
    )


@router.get("/{repo_id}/snapshot", response_model=AnalysisSnapshot)
async def get_latest_snapshot(
    repo_id: UUID, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)
) -> AnalysisSnapshot:
    repo = await session.get(Repo, repo_id)
    if repo is None or repo.user_id != current_user.id:
        raise HTTPException(404, "repo not found")
    if repo.latest_snapshot_id is None:
        raise HTTPException(404, "repo has no snapshot yet")
    snapshot = await session.get(AnalysisSnapshot, repo.latest_snapshot_id)
    if snapshot is None:
        raise HTTPException(404, "snapshot not found")
    return snapshot


@router.get("/{repo_id}/study-guide")
async def get_repo_study_guide(
    repo_id: UUID, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)
) -> RedirectResponse:
    """`StudyGuide.id` is generated inside the worker (study_guide_builder.py)
    and never surfaces through create_repo's response, the snapshot endpoint
    above, or the progress WebSocket — GET /study-guides/{id} (the only
    route that can actually fetch one) is otherwise unreachable without
    direct DB access (found via Codex's Phase 3 pre-push review). Redirects
    to the canonical resource instead of duplicating its response-building
    logic here.
    """
    repo = await session.get(Repo, repo_id)
    if repo is None or repo.user_id != current_user.id or repo.latest_snapshot_id is None:
        raise HTTPException(404, "repo has no snapshot yet")
    guide = (
        await session.exec(select(StudyGuide).where(StudyGuide.snapshot_id == repo.latest_snapshot_id))
    ).first()
    if guide is None:
        raise HTTPException(404, "study guide not ready yet")
    return RedirectResponse(f"/study-guides/{guide.id}")


class StudyGuideVersionOut(BaseModel):
    id: UUID
    version: int
    generated_at: datetime
    snapshot_id: UUID
    commit_hash: str | None


@router.get("/{repo_id}/study-guides", response_model=list[StudyGuideVersionOut])
async def list_repo_study_guides(
    repo_id: UUID, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)
) -> list[StudyGuideVersionOut]:
    """Every generated version of this repo's guide, newest first — the version
    picker for the architectural diff (#72).

    Only the identifying fields, not the guides themselves: this is a list to
    choose *from*, and returning N full guides with all their sections and
    citations to populate two dropdowns would be several megabytes to render a
    pair of labels.

    Sorted by version rather than generated_at because version is what the diff
    endpoint orders by when it decides which side is "before" — sorting the
    picker one way and the comparison another would let the UI label a diff
    backwards.
    """
    repo = await session.get(Repo, repo_id)
    if repo is None or repo.user_id != current_user.id:
        raise HTTPException(404, "repo not found")

    guides = list(
        (
            await session.exec(
                select(StudyGuide).where(StudyGuide.repo_id == repo_id).order_by(StudyGuide.version.desc())
            )
        ).all()
    )
    if not guides:
        return []

    # Same no-relationships pattern as the rest of this module: the commit is
    # the snapshot's, and the picker labels versions by it ("v2 · a1b2c3d").
    # Two columns rather than whole rows, for the same reason this endpoint
    # doesn't return whole guides — a snapshot carries its entire
    # `dependency_graph` JSON, which is a lot of payload for a 7-character label.
    commit_by_snapshot = dict(
        (
            await session.exec(
                select(AnalysisSnapshot.id, AnalysisSnapshot.commit_hash).where(
                    AnalysisSnapshot.id.in_({g.snapshot_id for g in guides})
                )
            )
        ).all()
    )

    return [
        StudyGuideVersionOut(
            id=guide.id,
            version=guide.version,
            generated_at=guide.generated_at,
            snapshot_id=guide.snapshot_id,
            commit_hash=commit_by_snapshot.get(guide.snapshot_id),
        )
        for guide in guides
    ]


@router.get("/{repo_id}/quiz")
async def get_repo_quiz(
    repo_id: UUID, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)
) -> RedirectResponse:
    """The most recently generated quiz for this repo, whatever its status
    (generating/ready/failed) — RepoDetail.tsx calls this on mount so a page
    reload, a second tab, or navigating away and back can recover an
    already-enqueued or already-ready quiz instead of only ever offering
    "Generate Quiz" again, which would enqueue a second paid generation job
    on top of one that may already be running or done (found via the Phase 5
    Codex review). Same redirect-to-canonical-resource shape as
    get_repo_study_guide above.
    """
    repo = await session.get(Repo, repo_id)
    if repo is None or repo.user_id != current_user.id:
        raise HTTPException(404, "repo not found")
    quiz = (await session.exec(select(Quiz).where(Quiz.repo_id == repo_id).order_by(Quiz.created_at.desc()))).first()
    if quiz is None:
        raise HTTPException(404, "no quiz generated yet")
    return RedirectResponse(f"/quizzes/{quiz.id}")


@router.websocket("/{repo_id}/progress")
async def repo_progress(
    repo_id: UUID,
    websocket: WebSocket,
    session: AsyncSession = Depends(get_session),
    redis: ArqRedis = Depends(get_redis_pool),
) -> None:
    await websocket.accept()

    # A WebSocket handshake carries cookies the same way a normal HTTP
    # request does, but Depends(get_current_user) raises HTTPException,
    # which has no meaning once the connection is already accepted — a
    # WebSocket rejection is a close code instead, so this reads the same
    # cookie manually rather than reusing that dependency directly.
    session_token = websocket.cookies.get("session_token")
    current_user = await get_user_from_token(session, session_token) if session_token else None
    if current_user is None:
        await websocket.close(code=4401)
        return

    repo = await session.get(Repo, repo_id)
    if repo is None or repo.user_id != current_user.id or repo.latest_snapshot_id is None:
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

        # A timed get_message loop rather than `async for pubsub.listen()`
        # (#9): pub/sub is fire-and-forget, so a Redis failure on the worker's
        # single terminal publish left a client that had already passed the
        # DB check above waiting forever for a message that would never
        # arrive — even though the snapshot was genuinely done. Every timeout
        # re-reads the row, which makes this correct regardless of *why* a
        # message was missed rather than only patching that one window.
        #
        # The publish path stays the fast one; this only bounds how long a
        # dropped message can go unnoticed.
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=PROGRESS_POLL_FALLBACK_SECONDS
            )
            if message is not None:
                payload = json.loads(message["data"])
                await websocket.send_json(payload)
                if payload["status"] in (SnapshotStatus.ready.value, SnapshotStatus.failed.value):
                    break
                continue

            if snapshot is None:
                continue
            # refresh, not session.get: the snapshot is already in this
            # session's identity map, so get() would keep handing back the
            # same stale in-memory object no matter what the worker committed.
            await session.refresh(snapshot)
            if snapshot.status in (SnapshotStatus.ready, SnapshotStatus.failed):
                await websocket.send_json({"status": snapshot.status.value})
                break
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe()
        await pubsub.aclose()
