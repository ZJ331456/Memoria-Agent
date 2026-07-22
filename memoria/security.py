from __future__ import annotations

import hmac
import time
from collections import defaultdict, deque

from fastapi import Request

from .config import Settings


class RequestGate:
    """Optional local API authentication, origin enforcement, body cap, and IP rate limit."""

    def __init__(self, settings: Settings):
        self.api_token = settings.api_token
        self.allowed_origins = set(settings.allowed_origins)
        self.rate_limit = settings.rate_limit_per_minute
        self.max_request_bytes = settings.max_request_bytes
        self._requests: dict[str, deque[float]] = defaultdict(deque)

    def check(self, request: Request) -> tuple[int, str, str, dict[str, str]] | None:
        path = request.url.path
        if request.method == "OPTIONS" or (not path.startswith("/api") and path != "/metrics") or path == "/api/health":
            return None
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > self.max_request_bytes:
                    return 413, "payload_too_large", "请求体超过服务器限制", {}
            except ValueError:
                return 400, "invalid_content_length", "Content-Length 无效", {}
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD"} and origin and origin not in self.allowed_origins:
            return 403, "origin_forbidden", "请求 Origin 不在允许列表", {}
        if self.api_token:
            authorization = request.headers.get("authorization", "")
            supplied = authorization[7:] if authorization.lower().startswith("bearer ") else request.headers.get("x-api-key", "")
            if not supplied or not hmac.compare_digest(supplied, self.api_token):
                return 401, "unauthorized", "API Token 缺失或无效", {"WWW-Authenticate": "Bearer"}
        if self.rate_limit:
            client = request.client.host if request.client else "unknown"
            current = time.monotonic()
            bucket = self._requests[client]
            while bucket and bucket[0] <= current - 60:
                bucket.popleft()
            if len(bucket) >= self.rate_limit:
                return 429, "rate_limited", "请求频率超过限制", {"Retry-After": "60"}
            bucket.append(current)
        return None
