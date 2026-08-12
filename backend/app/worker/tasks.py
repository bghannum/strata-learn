from typing import ClassVar

from arq.connections import RedisSettings

from app.config import settings
from app.worker.pipeline import index_repo


async def health_check(ctx: dict) -> str:
    return "ok"


class WorkerSettings:
    functions: ClassVar = [health_check, index_repo]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    # arq's default (300s) was fine for Phase 1's clone+parse-only pipeline,
    # but Phase 2's Layer B makes one real LLM call per module sequentially
    # (plus pattern detection + trade-off extraction) — found via the Phase 2
    # manual checkpoint to blow past 300s on a real ~50-file repo. 30 minutes
    # gives real indexing runs headroom without masking a genuinely hung job
    # forever (index_repo's CancelledError handler still fails the snapshot
    # cleanly if this is ever exceeded).
    job_timeout = 1800
