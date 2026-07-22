from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .config import Settings
from .llm import LLMClient
from .service import AgentService
from .security import RequestGate
from .observability import MetricRegistry
from .store import Store

MemoryKind = Literal["fact", "preference", "profile", "goal", "procedure"]


class StrictModel(BaseModel):
    """Public request bodies reject unknown fields instead of silently ignoring them."""

    model_config = ConfigDict(extra="forbid")


class ErrorResponse(BaseModel):
    code: str
    message: str
    request_id: str


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str


class SessionBody(StrictModel):
    title: str = Field(default="新对话", min_length=1, max_length=80, examples=["Rust 学习计划"])


class SessionPatch(StrictModel):
    title: str = Field(min_length=1, max_length=80)


class SessionResponse(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int = 0


class MessageResponse(BaseModel):
    id: str
    session_id: str
    role: Literal["user", "assistant"]
    content: str
    created_at: str


class ChatBody(StrictModel):
    content: str = Field(min_length=1, max_length=20000, examples=["请记住我偏好简洁回答"])


class MemoryBody(StrictModel):
    content: str = Field(min_length=1, max_length=4000)
    kind: MemoryKind = "fact"
    importance: int = Field(default=3, ge=1, le=5)


class MemoryPatch(StrictModel):
    content: str | None = Field(default=None, min_length=1, max_length=4000)
    kind: MemoryKind | None = None
    importance: int | None = Field(default=None, ge=1, le=5)


class MemoryResponse(BaseModel):
    id: str
    content: str
    kind: MemoryKind
    importance: int
    source: str
    created_at: str
    updated_at: str
    status: Literal["active", "superseded"] = "active"
    reinforcement: int = 1
    supersedes_id: str | None = None
    last_reinforced_at: str | None = None


class MemoryReindexResponse(BaseModel):
    enabled: bool
    indexed: int
    remaining: int


class MemoryWriteResponse(BaseModel):
    action: Literal["created", "reinforced", "superseded"]
    memory: MemoryResponse
    previous_id: str | None = None
    reason: str = ""


class MemoryReplacementResponse(BaseModel):
    id: int
    old_memory_id: str
    new_memory_id: str
    old_content: str
    new_content: str
    relation: str
    reason: str
    created_at: str


class TraceResponse(BaseModel):
    id: str
    session_id: str
    status: str
    steps: int
    duration_ms: int
    memories: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    created_at: str


class ChatResponse(BaseModel):
    message: MessageResponse
    memories_created: list[MemoryResponse]
    trace: TraceResponse


class CancelResponse(BaseModel):
    status: Literal["cancelled", "idle"]
    session_id: str


class MemoryUndoBody(StrictModel):
    source_refs: list[str] = Field(min_length=1, max_length=100)
    dry_run: bool = False


class MemoryUndoResponse(BaseModel):
    affected_ids: list[str]
    restored_ids: list[str]


class MemoryJobResponse(BaseModel):
    id: str
    source_ref: str
    status: str
    attempts: int
    error: str | None = None
    available_at: str | None = None
    lease_owner: str | None = None
    lease_expires_at: str | None = None
    created_at: str
    updated_at: str


class ToolExecuteBody(StrictModel):
    arguments: dict[str, Any] = Field(default_factory=dict)
    confirm_write: bool = False


class ToolExecuteResponse(BaseModel):
    name: str
    ok: bool
    content: str
    elapsed_ms: int


VERSION = "0.6.0"
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
    store = Store(settings.database, settings.vector_backend)
    service = AgentService(settings, store, LLMClient(settings))
    session_locks: dict[str, asyncio.Lock] = {}
    active_turns: dict[str, asyncio.Task] = {}
    request_gate = RequestGate(settings)
    metrics = MetricRegistry()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        memory_task = asyncio.create_task(service.memory_worker.run(), name="memoria-memory-worker")
        yield
        memory_task.cancel()
        with suppress(asyncio.CancelledError): await memory_task
        for task in active_turns.values(): task.cancel()
        if active_turns: await asyncio.gather(*active_turns.values(), return_exceptions=True)
        store.close()

    app = FastAPI(
        title="Memoria Agent API", version=VERSION,
        summary="带长期记忆、工具循环和可观测 trace 的本地 Agent API",
        description="API 默认挂载在 `/api`。交互式文档：`/docs`；OpenAPI JSON：`/openapi.json`。",
        openapi_tags=TAGS, lifespan=lifespan,
        responses={422: {"model": ErrorResponse, "description": "请求校验失败"}},
    )
    app.state.settings, app.state.store, app.state.service, app.state.metrics = settings, store, service, metrics
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.allowed_origins), allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"], allow_headers=["Content-Type", "X-Request-ID", "Authorization", "X-API-Key"])

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        started = time.perf_counter()
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id
        denied = request_gate.check(request)
        if denied:
            status, code, message, headers = denied
            response = _error(request, status, code, message)
            response.headers.update(headers)
        else:
            response = await call_next(request)
        duration = time.perf_counter() - started
        route = getattr(request.scope.get("route"), "path", None) or re.sub(r"/[A-Fa-f0-9-]{16,}(?=/|$)", "/{id}", request.url.path)
        metrics.observe_http(request.method, route, response.status_code, duration)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Process-Time-Ms"] = str(int(duration * 1000))
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

    @app.get("/metrics", include_in_schema=False)
    def prometheus_metrics():
        if not settings.metrics_enabled:
            raise HTTPException(404, "metrics disabled")
        return Response(metrics.render(store), media_type="text/plain; version=0.0.4")

    @app.get("/api/overview", tags=["system"], summary="获取 Dashboard 总览")
    def overview() -> dict[str, Any]:
        return {**store.overview(), "models": settings.public_dict(), "vector_index": store.vector_index_status, "tools": service.runtime.tools.catalog(), "pipeline": service.runtime.pipeline.inspect()}

    @app.get("/api/tools", tags=["tools"], summary="列出模型可调用工具")
    def tools() -> list[dict[str, str]]:
        return service.runtime.tools.catalog()

    @app.post("/api/tools/{tool_name}/execute", response_model=ToolExecuteResponse, tags=["tools"], summary="调试执行一个工具")
    async def execute_tool(tool_name: str, body: ToolExecuteBody):
        catalog = {item["name"]: item for item in service.runtime.tools.catalog()}
        tool = catalog.get(tool_name)
        if not tool: raise HTTPException(404, "工具不存在")
        if tool["risk"] != "read-only" and not body.confirm_write: raise HTTPException(409, "写工具需要 confirm_write=true")
        return await service.runtime.tools.execute(tool_name, body.arguments, allow_write=body.confirm_write)

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
                task = asyncio.create_task(service.chat_with_trace(session_id, body.content.strip()))
                active_turns[session_id] = task
                message, memories, trace = await task
            return {"message": message, "memories_created": memories, "trace": trace}
        except HTTPException: raise
        except Exception as exc: raise HTTPException(502, f"模型调用失败：{type(exc).__name__}: {exc}") from exc
        finally:
            active_turns.pop(session_id, None)
            if not lock.locked(): session_locks.pop(session_id, None)

    @app.post("/api/sessions/{session_id}/chat/stream", tags=["agent"], summary="以 SSE 流式执行一轮 Agent 对话")
    async def chat_stream(session_id: str, body: ChatBody, request: Request):
        if not store.session(session_id): raise HTTPException(404, "会话不存在")
        lock = session_locks.setdefault(session_id, asyncio.Lock())
        if lock.locked() or session_id in active_turns: raise HTTPException(409, "该会话已有一轮对话正在执行")
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        async def emit(event: dict[str, Any]): await queue.put(event)
        async def worker():
            try:
                async with lock:
                    message, memories, trace = await service.chat_with_trace(session_id, body.content.strip(), emit)
                await queue.put({"type":"complete","message":message,"memories_created":memories,"trace":trace})
            except asyncio.CancelledError:
                await queue.put({"type":"cancelled","session_id":session_id})
                raise
            except Exception as exc:
                await queue.put({"type":"error","message":f"{type(exc).__name__}: {exc}"})
            finally:
                active_turns.pop(session_id, None)
                if not lock.locked(): session_locks.pop(session_id, None)
                await queue.put(None)

        task = asyncio.create_task(worker())
        active_turns[session_id] = task

        async def events():
            try:
                while True:
                    if await request.is_disconnected():
                        task.cancel()
                        break
                    try: event = await asyncio.wait_for(queue.get(), timeout=0.5)
                    except asyncio.TimeoutError: continue
                    if event is None: break
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            finally:
                if not task.done(): task.cancel()
                with suppress(asyncio.CancelledError): await task
        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

    @app.post("/api/sessions/{session_id}/cancel", response_model=CancelResponse, tags=["agent"], summary="取消正在执行的 Agent turn")
    async def cancel_turn(session_id: str):
        task = active_turns.get(session_id)
        if not task or task.done(): return {"status":"idle","session_id":session_id}
        task.cancel()
        return {"status":"cancelled","session_id":session_id}

    @app.delete("/api/sessions/{session_id}", status_code=204, tags=["sessions"], summary="删除会话及其消息")
    def delete_session(session_id: str):
        if not store.delete_session(session_id): raise HTTPException(404, "会话不存在")
        return Response(status_code=204)

    @app.get("/api/memories", response_model=list[MemoryResponse], tags=["memories"], summary="搜索长期记忆")
    async def memories(q: str = Query(default="", max_length=200), limit: int = Query(default=100, ge=1, le=500), status: Literal["active", "superseded", "all"] = "active"):
        if q.strip() and status == "active":
            return await service.runtime.memory.retrieve(q, limit)
        return store.memories(q, limit, status)

    @app.post("/api/memories", response_model=MemoryWriteResponse, tags=["memories"], summary="创建、强化或替代长期记忆")
    async def create_memory(body: MemoryBody):
        if not body.content.strip(): raise HTTPException(422, "记忆内容不能为空")
        result = await service.runtime.memory.remember(body.content, body.kind, body.importance, "manual")
        return result.public_dict()

    @app.post("/api/memories/reindex", response_model=MemoryReindexResponse, tags=["memories"], summary="回填长期记忆语义向量")
    async def reindex_memories(limit: int = Query(default=1000, ge=1, le=5000)):
        return await service.runtime.memory.reindex(limit)

    @app.get("/api/memory-jobs", response_model=list[MemoryJobResponse], tags=["memories"], summary="查询后台记忆任务")
    def memory_jobs(limit: int = Query(default=50, ge=1, le=200)):
        return store.memory_jobs(limit)

    @app.post("/api/memory-jobs/{job_id}/retry", response_model=MemoryJobResponse, tags=["memories"], summary="重试失败的记忆任务")
    def retry_memory_job(job_id: str):
        if not store.retry_memory_job(job_id):
            raise HTTPException(409, "只有 failed 任务可以手动重试")
        return store.memory_job(job_id)

    @app.post("/api/memories/undo", response_model=MemoryUndoResponse, tags=["memories"], summary="按消息来源撤销自动记忆")
    def undo_memories(body: MemoryUndoBody):
        return store.undo_memory_sources(body.source_refs, body.dry_run)

    @app.get("/api/memories/{memory_id}/history", response_model=list[MemoryReplacementResponse], tags=["memories"], summary="查询记忆替代历史")
    def memory_history(memory_id: str):
        if not store.memory(memory_id): raise HTTPException(404, "记忆不存在")
        return store.memory_history(memory_id)

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
    return {401: "unauthorized", 403: "forbidden", 404: "not_found", 409: "conflict", 413: "payload_too_large", 429: "rate_limited", 502: "upstream_error"}.get(status, "request_error")
