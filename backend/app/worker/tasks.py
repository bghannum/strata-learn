from typing import ClassVar

from arq.connections import RedisSettings

from app.config import settings
from app.worker.pipeline import index_repo


async def health_check(ctx: dict) -> str:
    return "ok"


class WorkerSettings:
    functions: ClassVar = [health_check, index_repo]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
