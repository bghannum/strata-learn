"""Per-user hourly cap on audio provider calls — the cheapest credible
"rate/cost limiting" the Phase 8 plan asks for, and the first rate limit of
any kind in this codebase (login rate limiting is deferred to
productionization; see README).

Fixed hourly window keyed on user id: one Redis INCR, with an EXPIRE set the
first time the key is created. Not a sliding window and not a token bucket —
those are better limiters, but this bounds a single user's spend against a
paid provider on a single-user app, and the two-command version is the one
whose failure modes are obvious. Redis is already a hard dependency (arq),
so this adds no infrastructure.
"""

from datetime import UTC, datetime
from uuid import UUID

from redis.asyncio import Redis

WINDOW_SECONDS = 60 * 60


def _key(user_id: UUID, capability: str, now: datetime) -> str:
    return f"voice-rate:{capability}:{user_id}:{now.strftime('%Y%m%d%H')}"


async def check_and_count(redis: Redis, user_id: UUID, capability: str, limit: int) -> bool:
    """Counts this call and returns True if it's within the hourly limit.
    Counts *before* the paid call so a burst that trips the limit doesn't
    also get billed; the one call that pushes the count past `limit` is the
    first one refused."""
    key = _key(user_id, capability, datetime.now(UTC))
    count = await redis.incr(key)
    if count == 1:
        # Only the first increment sets the TTL, so a busy hour can't keep
        # extending its own window. Slightly longer than the window so a
        # key created at :59:59 still expires cleanly.
        await redis.expire(key, WINDOW_SECONDS + 60)
    return count <= limit
