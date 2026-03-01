"""Redis-backed rate limiting middleware with global limits."""

import math
import time
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import Request, Response
from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from lightspeed_agent.config import get_settings


class RedisRateLimiter:
    """Distributed Redis rate limiter using atomic Lua + ZSET sliding windows."""

    # KEYS: alternating minute/hour keys for each principal
    # ARGV: now_ms, minute_window_ms, minute_limit, hour_window_ms, hour_limit, member
    LUA_CHECK_AND_INCREMENT = """
local now_ms = tonumber(ARGV[1])
local minute_window_ms = tonumber(ARGV[2])
local minute_limit = tonumber(ARGV[3])
local hour_window_ms = tonumber(ARGV[4])
local hour_limit = tonumber(ARGV[5])
local member = ARGV[6]

local min_remaining_minute = minute_limit
local min_remaining_hour = hour_limit

for i = 1, #KEYS, 2 do
    local minute_key = KEYS[i]
    local hour_key = KEYS[i + 1]
    local minute_min = now_ms - minute_window_ms
    local hour_min = now_ms - hour_window_ms

    redis.call("ZREMRANGEBYSCORE", minute_key, 0, minute_min)
    redis.call("ZREMRANGEBYSCORE", hour_key, 0, hour_min)

    local minute_count = redis.call("ZCARD", minute_key)
    local hour_count = redis.call("ZCARD", hour_key)

    if minute_count >= minute_limit then
        local oldest = redis.call("ZRANGE", minute_key, 0, 0, "WITHSCORES")
        local retry_after_ms = minute_window_ms
        if oldest[2] then
            retry_after_ms = minute_window_ms - (now_ms - tonumber(oldest[2]))
            if retry_after_ms < 0 then
                retry_after_ms = 0
            end
        end
        return {0, "per_minute", minute_count, hour_count, retry_after_ms, i}
    end

    if hour_count >= hour_limit then
        local oldest = redis.call("ZRANGE", hour_key, 0, 0, "WITHSCORES")
        local retry_after_ms = hour_window_ms
        if oldest[2] then
            retry_after_ms = hour_window_ms - (now_ms - tonumber(oldest[2]))
            if retry_after_ms < 0 then
                retry_after_ms = 0
            end
        end
        return {0, "per_hour", minute_count, hour_count, retry_after_ms, i}
    end

    local minute_remaining = minute_limit - (minute_count + 1)
    local hour_remaining = hour_limit - (hour_count + 1)
    if minute_remaining < min_remaining_minute then
        min_remaining_minute = minute_remaining
    end
    if hour_remaining < min_remaining_hour then
        min_remaining_hour = hour_remaining
    end
end

for i = 1, #KEYS, 2 do
    local minute_key = KEYS[i]
    local hour_key = KEYS[i + 1]
    redis.call("ZADD", minute_key, now_ms, member)
    redis.call("ZADD", hour_key, now_ms, member)
    redis.call("PEXPIRE", minute_key, minute_window_ms)
    redis.call("PEXPIRE", hour_key, hour_window_ms)
end

return {1, "ok", min_remaining_minute, min_remaining_hour, 0, 0}
"""

    def __init__(self) -> None:
        settings = get_settings()
        timeout_seconds = max(settings.rate_limit_redis_timeout_ms, 1) / 1000.0
        self._redis = Redis.from_url(
            settings.rate_limit_redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_timeout=timeout_seconds,
            socket_connect_timeout=timeout_seconds,
        )
        self._requests_per_minute = settings.rate_limit_requests_per_minute
        self._requests_per_hour = settings.rate_limit_requests_per_hour
        self._key_prefix = settings.rate_limit_key_prefix

    async def verify_connection(self) -> None:
        """Fail fast when Redis is not reachable."""
        await self._redis.ping()

    async def close(self) -> None:
        """Close Redis resources."""
        await self._redis.aclose()

    async def is_allowed(
        self,
        *,
        principal_keys: list[str],
    ) -> tuple[bool, dict[str, int | str]]:
        """Check and atomically increment counters for all applicable principals."""
        if not principal_keys:
            raise ValueError("principal_keys must not be empty")

        now_ms = int(time.time() * 1000)
        unique_member = f"{now_ms}:{uuid.uuid4().hex}"
        redis_keys: list[str] = []
        for principal_key in principal_keys:
            redis_keys.append(f"{self._key_prefix}:{principal_key}:m")
            redis_keys.append(f"{self._key_prefix}:{principal_key}:h")

        try:
            result = await self._redis.eval(
                self.LUA_CHECK_AND_INCREMENT,
                len(redis_keys),
                *redis_keys,
                now_ms,
                60_000,
                self._requests_per_minute,
                3_600_000,
                self._requests_per_hour,
                unique_member,
            )
        except RedisError as exc:
            raise RuntimeError("Redis rate limiter check failed") from exc

        allowed = bool(int(result[0]))
        exceeded = str(result[1])
        principal_key_index = int(result[5])
        limited_principal = (
            principal_keys[(principal_key_index - 1) // 2]
            if principal_key_index > 0
            else "none"
        )

        if allowed:
            minute_remaining = int(result[2])
            hour_remaining = int(result[3])
            return True, {
                "requests_this_minute": self._requests_per_minute - minute_remaining,
                "requests_this_hour": self._requests_per_hour - hour_remaining,
                "limit_per_minute": self._requests_per_minute,
                "limit_per_hour": self._requests_per_hour,
                "exceeded": "ok",
                "retry_after": 0,
                "limited_principal": "none",
            }

        minute_count = int(result[2])
        hour_count = int(result[3])
        retry_after_ms = int(result[4])

        return False, {
            "requests_this_minute": minute_count,
            "requests_this_hour": hour_count,
            "limit_per_minute": self._requests_per_minute,
            "limit_per_hour": self._requests_per_hour,
            "exceeded": exceeded,
            "retry_after": int(math.ceil(retry_after_ms / 1000)),
            "limited_principal": limited_principal,
        }


# Global Redis rate limiter instance
_rate_limiter: RedisRateLimiter | None = None


def get_redis_rate_limiter() -> RedisRateLimiter:
    """Get or create the global Redis rate limiter."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RedisRateLimiter()
    return _rate_limiter


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware for global Redis-backed rate limiting."""

    # Paths to skip rate limiting
    SKIP_PATHS = {
        "/health",
        "/healthz",
        "/ready",
        "/metrics",
        "/.well-known/agent.json",
        "/docs",
        "/openapi.json",
        "/redoc",
    }

    # Paths that should be rate limited (A2A JSON-RPC endpoint)
    RATE_LIMITED_PATHS = {"/"}

    def __init__(self, app: Any):
        super().__init__(app)
        self._limiter = get_redis_rate_limiter()

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Any],
    ) -> Response:
        """Process request with rate limiting."""
        path = request.url.path

        # Skip rate limiting for non-API paths
        if self._should_skip(path):
            return await call_next(request)

        # Check rate limit
        principals = self._resolve_principals(request)
        try:
            allowed, status = await self._limiter.is_allowed(principal_keys=principals)
        except RuntimeError:
            return JSONResponse(
                status_code=503,
                content={
                    "error": "rate_limiter_unavailable",
                    "message": "Rate limiter backend unavailable",
                },
            )

        if not allowed:
            return self._rate_limit_response(status)

        # Process request
        response = await call_next(request)

        # Add rate limit headers
        response.headers["X-RateLimit-Limit"] = str(status["limit_per_minute"])
        response.headers["X-RateLimit-Remaining"] = str(
            max(0, int(status["limit_per_minute"]) - int(status["requests_this_minute"]))
        )

        return response

    def _should_skip(self, path: str) -> bool:
        """Check if path should skip rate limiting."""
        if path in self.SKIP_PATHS:
            return True

        # Only rate limit specific paths
        for rate_limited_path in self.RATE_LIMITED_PATHS:
            if path == rate_limited_path or path.startswith(f"{rate_limited_path}/"):
                return False

        return True

    @staticmethod
    def _resolve_principals(request: Request) -> list[str]:
        """Build all principal keys used for multi-dimensional rate limiting."""
        principals: list[str] = []

        order_id = getattr(request.state, "order_id", None)
        if order_id:
            principals.append(f"order:{order_id}")

        user = getattr(request.state, "user", None)
        if user is not None:
            user_id = getattr(user, "user_id", None)
            if user_id:
                principals.append(f"user:{user_id}")
            else:
                client_id = getattr(user, "client_id", None)
                if client_id:
                    principals.append(f"client:{client_id}")

        if principals:
            return principals

        client_ip = request.client.host if request.client else "unknown"
        return [f"ip:{client_ip}"]

    def _rate_limit_response(self, status: dict[str, int | str]) -> JSONResponse:
        """Build rate limit exceeded response."""
        retry_after = status.get("retry_after", 60)

        return JSONResponse(
            status_code=429,
            content={
                "error": "rate_limit_exceeded",
                "message": (
                    f"Rate limit exceeded ({status.get('exceeded', 'unknown')}) "
                    f"for {status.get('limited_principal', 'unknown')}"
                ),
                "retry_after": retry_after,
            },
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(status["limit_per_minute"]),
                "X-RateLimit-Remaining": "0",
            },
        )
