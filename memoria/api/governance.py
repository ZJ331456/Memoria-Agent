"""HTTP boundary for reviewed, agent-scoped shared memory."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..config import Settings
from ..governance import MemoryGovernance
from ..store import Store


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentBody(StrictBody):
    name: str = Field(min_length=1, max_length=80)


class SpaceBody(StrictBody):
    name: str = Field(min_length=1, max_length=100)
    visibility: Literal["private", "shared"] = "shared"


class GrantBody(StrictBody):
    agent_id: str = Field(min_length=1, max_length=80)
    role: Literal["reader", "contributor", "curator"]


class ProposalBody(StrictBody):
    space_id: str = Field(min_length=1, max_length=80)
    content: str = Field(min_length=1, max_length=4000)
    kind: Literal["fact", "preference", "profile", "goal", "procedure"] = "fact"
    importance: int = Field(default=3, ge=1, le=5)
    topic_key: str = Field(min_length=1, max_length=200)
    source_type: Literal["manual", "message", "external", "agent"] = "manual"
    source_ref: str | None = Field(default=None, max_length=500)
    expires_at: str | None = Field(default=None, max_length=40)


class ReasonBody(StrictBody):
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("审核或撤销原因不能只含空白")
        return value


class ReviewBody(ReasonBody):
    expected_replaces_id: str | None = Field(default=None, max_length=80)


class LegacyImportBody(StrictBody):
    actor_id: str = Field(min_length=1, max_length=80)
    topic_key: str = Field(min_length=1, max_length=200)


def governance_router(governance: MemoryGovernance, store: Store, settings: Settings) -> APIRouter:
    router = APIRouter()

    def admin(request: Request) -> None:
        # RequestGate validates the configured server token before this dependency.
        if settings.api_token:
            return
        peer = request.client.host if request.client else ""
        if peer not in {"127.0.0.1", "::1", "localhost", "testclient"}:
            raise HTTPException(403, "治理管理员接口仅允许本机调用；远程部署请配置 api_token")

    def actor(x_agent_key: str | None = Header(default=None, alias="X-Agent-Key")) -> dict:
        if not x_agent_key:
            raise HTTPException(401, "缺少 X-Agent-Key")
        item = governance.authenticate(x_agent_key)
        if item is None:
            raise HTTPException(401, "Agent key 无效或已禁用")
        return item

    @router.post("/api/governance/agents", tags=["governance"], summary="创建 Agent；密钥只返回一次")
    def create_agent(body: AgentBody, _: None = Depends(admin)):
        return governance.create_agent(body.name)

    @router.get("/api/governance/agents", tags=["governance"], summary="查看 Agent 注册信息")
    def list_agents(_: None = Depends(admin)):
        return governance.list_agents()

    @router.delete("/api/governance/agents/{agent_id}", status_code=204, tags=["governance"], summary="禁用 Agent 密钥")
    def disable_agent(agent_id: str, _: None = Depends(admin)):
        if not governance.disable_agent(agent_id):
            raise HTTPException(404, "Agent 不存在")

    @router.post("/api/governance/agents/{agent_id}/rotate-key", tags=["governance"], summary="轮换 Agent 密钥并恢复身份")
    def rotate_agent_key(agent_id: str, _: None = Depends(admin)):
        return governance.rotate_agent_key(agent_id)

    @router.post("/api/governance/spaces/{space_id}/import-memory/{memory_id}", tags=["governance"], summary="把本地记忆作为待审提案导入")
    def import_memory(space_id: str, memory_id: str, body: LegacyImportBody, _: None = Depends(admin)):
        item = store.memory(memory_id)
        if item is None or item.get("status") != "active":
            raise HTTPException(404, "有效的本地记忆不存在")
        return governance.propose(
            body.actor_id, space_id, item["content"], kind=item["kind"],
            importance=item["importance"], topic_key=body.topic_key,
            source_type="legacy_memory", source_ref=memory_id,
        )

    @router.post("/api/shared/spaces", tags=["shared memory"], summary="创建私有或共享记忆空间")
    def create_space(body: SpaceBody, agent: dict = Depends(actor)):
        return governance.create_space(agent["id"], body.name, body.visibility)

    @router.get("/api/shared/spaces", tags=["shared memory"], summary="列出 Agent 可见空间")
    def list_spaces(agent: dict = Depends(actor)):
        return governance.visible_spaces(agent["id"])

    @router.get("/api/shared/spaces/{space_id}/grants", tags=["shared memory"], summary="查看空间授权")
    def list_grants(space_id: str, agent: dict = Depends(actor)):
        return governance.list_grants(agent["id"], space_id)

    @router.post("/api/shared/spaces/{space_id}/grants", tags=["shared memory"], summary="授权空间成员")
    def grant(space_id: str, body: GrantBody, agent: dict = Depends(actor)):
        return governance.grant(agent["id"], space_id, body.agent_id, body.role)

    @router.delete("/api/shared/spaces/{space_id}/grants/{member_id}", tags=["shared memory"], summary="撤销空间成员权限")
    def revoke_grant(space_id: str, member_id: str, agent: dict = Depends(actor)):
        if not governance.revoke_grant(agent["id"], space_id, member_id):
            raise HTTPException(404, "授权不存在")
        return {"revoked": True}

    @router.post("/api/shared/proposals", tags=["shared memory"], summary="提交共享记忆提案")
    def propose(body: ProposalBody, agent: dict = Depends(actor)):
        return governance.propose(
            agent["id"], body.space_id, body.content, kind=body.kind,
            importance=body.importance, topic_key=body.topic_key,
            source_type=body.source_type, source_ref=body.source_ref,
            expires_at=body.expires_at,
        )

    @router.get("/api/shared/proposals", tags=["shared memory"], summary="查看共享记忆审核队列")
    def list_proposals(
        space_id: str = Query(min_length=1),
        status: Literal["pending", "approved", "rejected", "all"] = "pending",
        limit: int = Query(default=100, ge=1, le=200),
        agent: dict = Depends(actor),
    ):
        return governance.list_proposals(agent["id"], space_id, status, limit)

    @router.post("/api/shared/proposals/{proposal_id}/approve", tags=["shared memory"], summary="批准提案并生成当前记忆版本")
    def approve(proposal_id: str, body: ReviewBody, agent: dict = Depends(actor)):
        return governance.approve(agent["id"], proposal_id, body.reason, body.expected_replaces_id)

    @router.post("/api/shared/proposals/{proposal_id}/reject", tags=["shared memory"], summary="拒绝共享记忆提案")
    def reject(proposal_id: str, body: ReasonBody, agent: dict = Depends(actor)):
        return governance.reject(agent["id"], proposal_id, body.reason)

    @router.get("/api/shared/memories", tags=["shared memory"], summary="仅检索当前有权读取的生效记忆")
    def search(
        q: str = Query(default="", max_length=200),
        space_id: str | None = None,
        limit: int = Query(default=100, ge=1, le=200),
        agent: dict = Depends(actor),
    ):
        return governance.search(agent["id"], q, space_id, limit)

    @router.get("/api/shared/memories/{memory_id}", tags=["shared memory"], summary="读取有权访问的记忆详情")
    def detail(memory_id: str, agent: dict = Depends(actor)):
        return governance.detail(agent["id"], memory_id)

    @router.get("/api/shared/memories/{memory_id}/lineage", tags=["shared memory"], summary="查看授权范围内的版本链")
    def lineage(memory_id: str, agent: dict = Depends(actor)):
        return governance.lineage(agent["id"], memory_id)

    @router.post("/api/shared/memories/{memory_id}/revoke", tags=["shared memory"], summary="撤销已批准记忆")
    def revoke_memory(memory_id: str, body: ReasonBody, agent: dict = Depends(actor)):
        return governance.revoke_memory(agent["id"], memory_id, body.reason)

    @router.get("/api/shared/events", tags=["shared memory"], summary="查看空间治理审计记录")
    def events(space_id: str = Query(min_length=1), limit: int = Query(default=100, ge=1, le=200), agent: dict = Depends(actor)):
        return governance.events(agent["id"], space_id, limit)

    return router
