from arq.connections import RedisSettings

from app.config import settings


async def health_check(ctx: dict) -> str:
    return "ok"


class WorkerSettings:
    functions = [health_check]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
