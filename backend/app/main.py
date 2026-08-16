import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError

from app.api import attempts, auth, quizzes, repos, study_guides, voice
from app.audio.dependencies import describe_backends, warm_local_backends
from app.config import settings

logger = logging.getLogger("strata")

# uvicorn configures only its own loggers; the root stays at WARNING, so the
# app's INFO records (voice backend status at startup, per-call audio
# metering under "strata.voice") were silently dropped in the container.
# One handler on the "strata" parent, added only if nothing else has
# configured it (pytest's caplog, a future logging config), and INFO on.
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s: %(message)s"))
    logger.addHandler(_handler)
logger.setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # One line per audio capability at startup, saying which backend is on
    # or *why* it's off — the operator-facing half of app/audio/
    # dependencies.py's single degradation seam. Endpoints deliberately
    # never say why (their 503 detail is generic), so this log is the only
    # place "I set SPEECH_BACKEND=local and nothing happened" gets answered.
    for line in describe_backends():
        logger.info(line)
    # Local models load in the background so the first read-aloud click
    # isn't the one that waits on a cold download. Fire-and-forget; the
    # task is cancelled with the loop on shutdown.
    warm_task = asyncio.create_task(warm_local_backends()) if settings.voice_warm_on_startup else None
    yield
    if warm_task is not None and not warm_task.done():
        warm_task.cancel()


app = FastAPI(title="Strata Learn API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(repos.router)
app.include_router(study_guides.router)
app.include_router(quizzes.router)
app.include_router(attempts.router)
app.include_router(voice.router)


@app.exception_handler(RedisError)
async def redis_error_handler(request: Request, exc: RedisError) -> JSONResponse:
    # Covers Redis being unreachable at dependency-resolution time (e.g.
    # get_redis_pool's create_pool() itself failing) — before a route body
    # even starts, so nothing's been committed yet for this request to clean
    # up. api/repos.py's own try/except handles the separate case where Redis
    # fails *after* a repo/snapshot is already committed, which needs
    # fail_snapshot(), not just a clean error response.
    return JSONResponse(status_code=503, content={"detail": "Could not reach the job queue — try again shortly"})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
