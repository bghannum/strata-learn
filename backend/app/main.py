from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError

from app.api import auth, repos, study_guides
from app.config import settings

app = FastAPI(title="Strata Learn API")

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
