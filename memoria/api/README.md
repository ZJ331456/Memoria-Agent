# API：HTTP 与治理边界

[返回核心包](../README.md) · [个人运行时](../runtime/README.md) · [共享治理](../../docs/记忆治理与多Agent共享记忆.md) · [完整接口](../../docs/API接口文档.md)

## 文件映射

| 文件 | 职责 |
|---|---|
| [app.py](app.py) | `create_app`、Pydantic 契约、中间件、系统/会话/聊天/个人记忆/工具/Drift/MCP/Skills 路由、静态前端托管 |
| [governance.py](governance.py) | Agent 注册与密钥管理、空间授权、共享记忆提议/审批/检索/撤回及审计路由 |
| [__init__.py](__init__.py) | 稳定导出 `create_app` |
| [../security.py](../security.py) | 请求体上限、Origin、可选服务 Token 和限流 |

## 请求流程

1. `create_app` 加载 [Settings](../config.py)，构建 [Store](../store.py)、[AgentService](../service.py) 和 [MemoryGovernance](../governance.py)，将治理 router 挂入应用。
2. 中间件分配 `X-Request-ID`、执行 `RequestGate` 并记录 HTTP 指标。请求体用 Pydantic 校验；校验错误与显式业务异常返回 `{code, message, request_id}`。
3. 聊天路由调用 `AgentService.chat_with_trace`；流式路由发送 SSE。每个会话用独立锁限制并发 turn，取消路由停止活动任务。
4. 个人记忆路由使用 [MemoryEngine](../memory/README.md) 和审核队列；共享路由由 `X-Agent-Key` 认证，再由治理层在每次操作时执行空间 ACL。管理员创建/禁用/轮换 Agent key 及导入旧记忆走 `/api/governance`。
5. 生命周期启动/停止 MCP、自动记忆 worker、情景归档维护和 Drift worker；取消后台任务后关闭数据库，存在 `frontend/dist` 时托管构建产物。

## 主要路径与边界

| 路径 | 功能 |
|---|---|
| `/api/sessions`、`/api/sessions/{id}/chat/stream` | 会话管理、对话和 SSE |
| `/api/memories`、`/api/memory-reviews` | 个人记忆与自动提取审核 |
| `/api/memory-layers`、`/api/episodes` | 分层配置/统计与个人任务情景的回放、固定、归档和维护 |
| `/api/governance/agents`、`/api/governance/spaces/{id}/import-memory/{id}` | 管理员身份与旧记忆导入 |
| `/api/shared/spaces`、`/api/shared/proposals`、`/api/shared/memories`、`/api/shared/events` | 共享记忆空间、提议、查询和审计 |
| `/api/tools`、`/api/mcp`、`/api/skills`、`/api/drift`、`/api/traces` | 扩展与诊断 |
| `/docs`、`/openapi.json`、`/metrics` | 运行时接口文档与可选指标 |

`[server.security]` 定义服务 Token、Origin 白名单、限流和请求体上限。提供 `X-Agent-Key` 的 `/api/shared/*` 请求由 Agent key 独立认证，服务 Token 不等于 Agent 身份；`/api/governance/*` 管理员接口仍需服务 Token，未配置时仅允许本机来源。写工具调试执行另需 `confirm_write=true`，不等于共享空间审批。个人与共享记忆 API 的数据表、权限和召回路径保持分离。

`/api/episodes` 是个人治理视图，能回放过期归档；`recall_episodes` 仍只召回活动、未过期或固定的记录。管理读取不会将归档重新注入。共享 Agent key 不绕过这些新路径的全局认证。情景关闭时响应明确报告不可用，不替用户删除现有数据。

## 从仓库根目录验证

```bash
python -m pytest -q tests/test_core.py tests/test_memory_review.py tests/test_governance_api.py
python -m compileall -q memoria
```
