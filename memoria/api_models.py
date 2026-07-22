from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MemoryKind = Literal["fact", "preference", "profile", "goal", "procedure"]


class StrictModel(BaseModel):
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


class TraceResponse(BaseModel):
    id: str
    session_id: str
    status: str
    steps: int
    duration_ms: int
    memories: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    error: str | None = None
    created_at: str


class ChatResponse(BaseModel):
    message: MessageResponse
    memories_created: list[MemoryResponse]
    trace: TraceResponse


class ToolExecuteBody(StrictModel):
    arguments: dict[str, Any] = Field(default_factory=dict)
    confirm_write: bool = False


class ToolExecuteResponse(BaseModel):
    name: str
    ok: bool
    content: str
    elapsed_ms: int

