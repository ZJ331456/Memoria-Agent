"""Lightweight OpenAI-compatible HTTP server for Windows-native local inference.

vLLM / SGLang are Linux-first; on Windows use this server (or Ollama / WSL).
Memoria Agent should point llm.main.base_url at http://127.0.0.1:<port>/v1.
"""
from __future__ import annotations

import json
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .backends.process_engine import get_local_llm, model_path


def create_local_openai_app(
    *,
    model: str = "model/Qwen3.5-2B",
    device: str = "cuda",
    served_model_name: str | None = None,
) -> FastAPI:
    app = FastAPI(title="Memoria Local OpenAI Server", version="0.1.0")
    model_id = served_model_name or model
    engine = get_local_llm(str(model_path(model)), device)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "backend": "process_local", "device": device, "model": model_id}

    @app.get("/v1/models")
    async def list_models() -> dict[str, Any]:
        return {
            "object": "list",
            "data": [{"id": model_id, "object": "model", "owned_by": "memoria-local"}],
        }

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        body = await request.json()
        messages = body.get("messages") or []
        if not isinstance(messages, list) or not messages:
            raise HTTPException(status_code=400, detail="messages is required")
        max_tokens = int(body.get("max_tokens") or 256)
        tools = body.get("tools")
        stream = bool(body.get("stream"))
        try:
            data = await engine.chat(messages, tools, max_tokens, timeout=300)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc

        created = int(time.time())
        message = data.get("raw_message") or {"role": "assistant", "content": data.get("content") or ""}
        finish = data.get("finish_reason") or "stop"
        usage = data.get("usage") or {}

        if not stream:
            return {
                "id": f"chatcmpl-memoria-{created}",
                "object": "chat.completion",
                "created": created,
                "model": body.get("model") or model_id,
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": usage,
            }

        async def event_stream():
            content = str(data.get("content") or "")
            chunk = {
                "id": f"chatcmpl-memoria-{created}",
                "object": "chat.completion.chunk",
                "created": created,
                "model": body.get("model") or model_id,
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": content}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            # Emit tool_calls as a final non-delta OpenAI-shaped chunk if present.
            if data.get("tool_calls"):
                tool_chunk = {
                    "id": f"chatcmpl-memoria-{created}",
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": body.get("model") or model_id,
                    "choices": [{
                        "index": 0,
                        "delta": {"tool_calls": message.get("tool_calls") or []},
                        "finish_reason": None,
                    }],
                }
                yield f"data: {json.dumps(tool_chunk, ensure_ascii=False)}\n\n"
            end = {
                "id": f"chatcmpl-memoria-{created}",
                "object": "chat.completion.chunk",
                "created": created,
                "model": body.get("model") or model_id,
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                "usage": usage,
            }
            yield f"data: {json.dumps(end, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, exc: Exception):
        return JSONResponse(status_code=500, content={"error": {"message": str(exc)[:500]}})

    return app
