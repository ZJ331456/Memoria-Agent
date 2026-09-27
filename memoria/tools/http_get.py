from __future__ import annotations

from urllib.parse import urlparse

import httpx


DEFAULT_ALLOWED_HOSTS = (
    "wttr.in",
    "api.open-meteo.com",
    "open-meteo.com",
)


def host_allowed(host: str, allowed_hosts: tuple[str, ...]) -> bool:
    host = host.lower().rstrip(".")
    for item in allowed_hosts:
        needle = item.lower().lstrip(".")
        if host == needle or host.endswith("." + needle):
            return True
    return False


async def fetch_url(
    url: str,
    *,
    allowed_hosts: tuple[str, ...],
    timeout_seconds: float = 15.0,
    max_chars: int = 8000,
) -> dict:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("只允许 http/https URL")
    if not host_allowed(parsed.hostname or "", allowed_hosts):
        raise PermissionError(f"主机不在白名单：{parsed.hostname}")
    headers = {"User-Agent": "MemoriaAgent/1.0 (+local skills http_get)"}
    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True, headers=headers) as client:
        response = await client.get(url)
    text = response.text
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars]
    return {
        "url": str(response.url),
        "status_code": response.status_code,
        "content_type": response.headers.get("content-type", ""),
        "truncated": truncated,
        "text": text,
    }
