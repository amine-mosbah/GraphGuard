"""Refresh-token denylist — what makes stateless JWT authentication revocable.

Role
----
Records the ``jti`` of refresh tokens that must no longer be accepted: those explicitly logged
out, and those already exchanged for a new pair.

Why a denylist rather than a session table
------------------------------------------
Storing every issued token server-side would make authentication a database read on every
request, which is precisely the cost JWT exists to avoid. A denylist inverts the economics: the
common case (a valid token) costs nothing, and only the rare case (a revoked one) needs a lookup.
Redis is the right store because each entry has a natural expiry — once the token would have
expired anyway, the denylist entry is worthless, so entries are written with a TTL equal to the
token's remaining lifetime and the list cannot grow without bound.

Refresh-token rotation
----------------------
:func:`revoke` is called on *every* refresh exchange, not only on logout. That makes refresh
tokens single-use, which turns token theft into a detectable event: if both the attacker and the
legitimate user try to use the same refresh token, the second attempt fails. Without rotation, a
stolen refresh token grants access for its entire multi-day lifetime, silently.

Inputs / outputs
----------------
Input: token identifiers and their expiry instants.
Output: revocation records in Redis, and boolean revocation checks.
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from typing import Protocol

import redis

from graphguard.platform.settings import get_settings

#: Key prefix, so the denylist shares a Redis instance with the cache and the Celery broker
#: without colliding.
_KEY_PREFIX = "graphguard:revoked-jti:"


class RedisLike(Protocol):
    """The subset of the Redis client this module uses.

    Declared as a protocol so tests can inject ``fakeredis`` — or any stand-in — without the
    module depending on a running server.
    """

    def set(self, name: str, value: str, ex: int | None = ...) -> object:
        """Set ``name`` to ``value`` with an optional expiry in seconds."""
        ...

    def exists(self, name: str) -> int:
        """Return the number of matching keys (0 or 1 here)."""
        ...


@lru_cache(maxsize=1)
def get_redis() -> RedisLike:
    """Return the process-wide Redis client."""
    return redis.Redis.from_url(get_settings().redis_url, decode_responses=True)


def reset_redis_cache() -> None:
    """Drop the cached client, so tests can substitute a different backend."""
    get_redis.cache_clear()


def revoke(jti: str, expires_at: datetime, *, client: RedisLike | None = None) -> None:
    """Mark ``jti`` as no longer acceptable, until the moment it would have expired anyway.

    A token already past its expiry is not recorded: it is rejected by signature validation, so a
    denylist entry would occupy memory to prevent nothing.
    """
    client = client or get_redis()
    ttl = int((expires_at - datetime.now(UTC)).total_seconds())
    if ttl <= 0:
        return
    client.set(_KEY_PREFIX + jti, "1", ex=ttl)


def is_revoked(jti: str, *, client: RedisLike | None = None) -> bool:
    """Return whether ``jti`` has been revoked.

    On a Redis outage this raises rather than returning ``False``. That is deliberate: silently
    treating every token as valid when the revocation store is unreachable would turn an
    infrastructure failure into an authentication bypass, which is the more dangerous of the two
    ways to fail.
    """
    client = client or get_redis()
    return bool(client.exists(_KEY_PREFIX + jti))
