# Memoria API 单文件模块说明

## 1. 文件位置与目标

HTTP API 的全部代码集中在 [`api.py`](api.py)。原来的 `api_models.py` 已合并并删除，今后新增或修改接口时只需要检查一个文件，同时仍通过清晰的代码分区保留可维护性。

`api.py` 只负责 HTTP 边界，不实现 Agent 推理、记忆算法或 SQLite 业务逻辑。核心调用方向保持为：

```text
FastAPI route
  → AgentService / AgentRuntime / MemoryEngine
  → Store / LLMClient / ToolRegistry
```

## 2. 文件内部结构

| 顺序 | 区域 | 职责 |
|---|---|---|
| 1 | imports 与 `MemoryKind` | 框架依赖和公开枚举 |
| 2 | Pydantic models | 请求校验、响应过滤与 OpenAPI schema |
| 3 | `VERSION` 与 `TAGS` | API 版本和 Swagger 分组 |
| 4 | `create_app()` | 装配 Settings、Store、LLM 和 AgentService |
| 5 | middleware / handlers | Request ID、安全 header、统一错误结构 |
| 6 | routes | system、tools、traces、sessions、agent、memories |
| 7 | SPA mount | 生产构建存在时托管 React 前端 |
| 8 | `_error*` | 错误响应辅助函数 |

## 3. 请求与响应模型

所有写请求继承 `StrictModel`，其 `extra="forbid"` 会拒绝未知字段。例如把 `importance` 错写为 `important` 会返回 422，不会静默使用默认值。

主要契约包括：

- Session：`SessionBody`、`SessionPatch`、`SessionResponse`
- Chat：`ChatBody`、`ChatResponse`、`MessageResponse`
- Memory：`MemoryBody`、`MemoryPatch`、`MemoryResponse`、`MemoryWriteResponse`、`MemoryReindexResponse`、`MemoryReplacementResponse`
- Tool：`ToolExecuteBody`、`ToolExecuteResponse`
- Trace/System：`TraceResponse`、`HealthResponse`、`ErrorResponse`

响应模型不会包含数据库中的 embedding 原始向量，也不会包含配置文件里的 API Key。

## 4. 路由分组

| Tag | 路径范围 | 说明 |
|---|---|---|
| `system` | `/api/health`、`/api/overview` | 存活状态和脱敏运行时信息 |
| `sessions` | `/api/sessions...` | 会话和消息 CRUD |
| `agent` | `/api/sessions/{id}/chat` | 完整 Agent turn |
| `memories` | `/api/memories...` | 语义检索、强化/替代写入、编辑、删除、历史和向量回填 |
| `tools` | `/api/tools...` | 工具目录和受确认保护的调试执行 |
| `traces` | `/api/traces` | 推理运行追踪 |

详细字段、状态码和 curl 示例见项目级 [`docs/API接口文档.md`](../docs/API接口文档.md)。服务运行后可访问 `/docs` 查看 Swagger UI，访问 `/openapi.json` 获取机器可读契约。

## 5. 并发与错误边界

- 每个 session 使用独立 `asyncio.Lock`，同一会话的并发 turn 返回 409，不同会话可以并行。
- FastAPI 校验错误转换为统一 `{code, message, request_id}`。
- Agent 或模型上游异常转换为 502；404 和 409 保留明确业务语义。
- 所有响应携带 `X-Request-ID` 和 `X-Content-Type-Options: nosniff`。
- CORS 当前只允许本地 Vite 开发地址，适用于可信本机环境。

## 6. 修改 API 的检查清单

1. 在 `api.py` 中新增或修改 Pydantic 模型。
2. 为路由声明 `response_model`、tag、summary 和输入边界。
3. 更新 `docs/API接口文档.md` 与前端 `src/api.ts`。
4. 在 `tests/test_core.py` 增加成功、校验失败和权限边界测试。
5. 执行 `python -m pytest -q`、`python -m compileall -q memoria` 和前端 `npm run build`。

如果 API 以后增长到数千行，应按业务域拆成 router 包；在当前规模下，用户要求的单文件形式更便于查看和调试。
