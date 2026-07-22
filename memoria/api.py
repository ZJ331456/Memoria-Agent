from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .api_models import (
    ChatBody, ChatResponse, ErrorResponse, HealthResponse, MemoryBody, MemoryPatch,
    MemoryResponse, MessageResponse, SessionBody, SessionPatch, SessionResponse,
    ToolExecuteBody, ToolExecuteResponse, TraceResponse,
)
from .config import Settings
from .llm import LLMClient
from .service import AgentService
from .store import Store

VERSION = "0.2.0"
TAGS = [
    {"name": "system", "description": "健康检查、运行时能力和脱敏配置。"},
    {"name": "sessions", "description": "会话生命周期和消息历史。"},
    {"name": "agent", "description": "执行完整 Agent turn，包括记忆召回、工具循环和 trace。"},
    {"name": "memories", "description": "长期记忆查询、创建、编辑和删除。"},
    {"name": "tools", "description": "工具目录以及受风险级别保护的调试执行。"},
    {"name": "traces", "description": "每轮推理的耗时、召回与工具调用诊断。"},
]


def create_app(config_path: str | Path | None = None) -> FastAPI:
    settings = Settings.load(config_path)
    store = Store(settings.database)
    service = AgentService(settings, store, LLMClient(settings))
    session_locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        store.close()

    app = FastAPI(
        title="Memoria Agent API", version=VERSION,
        summary="带长期记忆、工具循环和可观测 trace 的本地 Agent API",
        description="API 默认挂载在 `/api`。交互式文档：`/docs`；OpenAPI JSON：`/openapi.json`。",
        openapi_tags=TAGS, lifespan=lifespan,
        responses={422: {"model": ErrorResponse, "description": "请求校验失败"}},
    )
    app.state.settings, app.state.store, app.state.service = settings, store, service
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"], allow_headers=["Content-Type", "X-Request-ID"])

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        return _error(request, 422, "validation_error", details)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return _error(request, exc.status_code, _error_code(exc.status_code), str(exc.detail))

    @app.get("/api/health", response_model=HealthResponse, tags=["system"], summary="存活检查")
    def health() -> HealthResponse:
        return HealthResponse(version=VERSION)

    @app.get("/api/overview", tags=["system"], summary="获取 Dashboard 总览")
    def overview() -> dict[str, Any]:
        return {**store.overview(), "models": settings.public_dict(), "tools": service.runtime.tools.catalog(), "pipeline": service.runtime.pipeline.inspect()}

    @app.get("/api/tools", tags=["tools"], summary="列出模型可调用工具")
    def tools() -> list[dict[str, str]]:
        return service.runtime.tools.catalog()

    @app.post("/api/tools/{tool_name}/execute", response_model=ToolExecuteResponse, tags=["tools"], summary="调试执行一个工具")
    async def execute_tool(tool_name: str, body: ToolExecuteBody):
        catalog = {item["name"]: item for item in service.runtime.tools.catalog()}
        tool = catalog.get(tool_name)
        if not tool: raise HTTPException(404, "工具不存在")
        if tool["risk"] != "read-only" and not body.confirm_write: raise HTTPException(409, "写工具需要 confirm_write=true")
        return await service.runtime.tools.execute(tool_name, body.arguments)

    @app.get("/api/traces", response_model=list[TraceResponse], tags=["traces"], summary="查询最近运行追踪")
    def traces(session_id: str = Query(default="", max_length=64), limit: int = Query(default=50, ge=1, le=200)):
        return store.traces(session_id, limit)

    @app.get("/api/sessions", response_model=list[SessionResponse], tags=["sessions"], summary="列出会话")
    def sessions(): return store.sessions()

    @app.post("/api/sessions", response_model=SessionResponse, status_code=201, tags=["sessions"], summary="创建会话")
    def create_session(body: SessionBody): return {**store.create_session(body.title), "message_count": 0}

    @app.get("/api/sessions/{session_id}", response_model=SessionResponse, tags=["sessions"], summary="获取会话")
    def session(session_id: str):
        item = store.session(session_id)
        if not item: raise HTTPException(404, "会话不存在")
        item["message_count"] = len(store.messages(session_id, 10000))
        return item

    @app.patch("/api/sessions/{session_id}", response_model=SessionResponse, tags=["sessions"], summary="重命名会话")
    def update_session(session_id: str, body: SessionPatch):
        if not store.session(session_id): raise HTTPException(404, "会话不存在")
        store.rename_session(session_id, body.title)
        item = store.session(session_id) or {}
        item["message_count"] = len(store.messages(session_id, 10000))
        return item

    @app.get("/api/sessions/{session_id}/messages", response_model=list[MessageResponse], tags=["sessions"], summary="获取会话消息")
    def messages(session_id: str, limit: int = Query(default=100, ge=1, le=1000)):
        if not store.session(session_id): raise HTTPException(404, "会话不存在")
        return store.messages(session_id, limit)

    @app.post("/api/sessions/{session_id}/chat", response_model=ChatResponse, tags=["agent"], summary="执行一轮 Agent 对话")
    async def chat(session_id: str, body: ChatBody):
        if not store.session(session_id): raise HTTPException(404, "会话不存在")
        lock = session_locks.setdefault(session_id, asyncio.Lock())
        if lock.locked(): raise HTTPException(409, "该会话已有一轮对话正在执行")
        try:
            async with lock:
                message, memories, trace = await service.chat_with_trace(session_id, body.content.strip())
            return {"message": message, "memories_created": memories, "trace": trace}
        except HTTPException: raise
        except Exception as exc: raise HTTPException(502, f"模型调用失败：{type(exc).__name__}: {exc}") from exc
        finally:
            if not lock.locked(): session_locks.pop(session_id, None)

    @app.delete("/api/sessions/{session_id}", status_code=204, tags=["sessions"], summary="删除会话及其消息")
    def delete_session(session_id: str):
        if not store.delete_session(session_id): raise HTTPException(404, "会话不存在")
        return Response(status_code=204)

    @app.get("/api/memories", response_model=list[MemoryResponse], tags=["memories"], summary="搜索长期记忆")
    def memories(q: str = Query(default="", max_length=200), limit: int = Query(default=100, ge=1, le=500)):
        return store.memories(q, limit)

    @app.post("/api/memories", response_model=MemoryResponse, status_code=201, tags=["memories"], summary="创建长期记忆")
    def create_memory(body: MemoryBody): return store.add_memory(body.content, body.kind, body.importance)

    @app.patch("/api/memories/{memory_id}", response_model=MemoryResponse, tags=["memories"], summary="编辑长期记忆")
    def update_memory(memory_id: str, body: MemoryPatch):
        item = store.update_memory(memory_id, body.model_dump(exclude_unset=True))
        if not item: raise HTTPException(404, "记忆不存在")
        return item

    @app.delete("/api/memories/{memory_id}", status_code=204, tags=["memories"], summary="删除长期记忆")
    def delete_memory(memory_id: str):
        if not store.delete_memory(memory_id): raise HTTPException(404, "记忆不存在")
        return Response(status_code=204)

    dist = settings.root / "frontend" / "dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            candidate = (dist / path).resolve()
            if path and candidate.is_relative_to(dist.resolve()) and candidate.is_file(): return FileResponse(candidate)
            return FileResponse(dist / "index.html")
    return app


def _error(request: Request, status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content=ErrorResponse(code=code, message=message, request_id=getattr(request.state, "request_id", "")).model_dump())


def _error_code(status: int) -> str:
    return {404: "not_found", 409: "conflict", 502: "upstream_error"}.get(status, "request_error")
