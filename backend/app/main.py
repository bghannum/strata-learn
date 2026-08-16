import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError

from app.api import attempts, auth, quizzes, repos, study_guides, voice
from app.audio.dependencies import describe_backends
from app.config import settings

logger = logging.getLogger("strata")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # One line per audio capability at startup, saying which backend is on
    # or *why* it's off — the operator-facing half of app/audio/
    # dependencies.py's single degradation seam. Endpoints deliberately
    # never say why (their 503 detail is generic), so this log is the only
    # place "I set SPEECH_BACKEND=local and nothing happened" gets answered.
    for line in describe_backends():
        logger.info(line)
    yield


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
