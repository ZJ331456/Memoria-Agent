from __future__ import annotations

from typing import Literal

import httpx

from ...config import ModelConfig
from .openai_http import OpenAICompatBackend, classify_openai_server
from .process import ProcessLocalBackend

BackendKind = Literal["openai_http", "process_local"]


def detect_backend_kind(config: ModelConfig) -> BackendKind:
    if config.is_local_process():
        return "process_local"
    return "openai_http"


def resolve_backend(
    config: ModelConfig,
    *,
    timeout_seconds: float,
    max_retries: int,
    transport: httpx.AsyncBaseTransport | None = None,
) -> OpenAICompatBackend | ProcessLocalBackend:
    kind = detect_backend_kind(config)
    if kind == "process_local":
        return ProcessLocalBackend(timeout_seconds=timeout_seconds)
    return OpenAICompatBackend(
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        transport=transport,
    )


def backend_public_label(config: ModelConfig) -> str:
    kind = detect_backend_kind(config)
    if kind == "process_local":
        return f"process_local:{config.base_url.removeprefix('local://') or 'auto'}"
    return classify_openai_server(config.base_url)
