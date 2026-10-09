# Memoria Agent API 接口文档

[文档导航](README.md) · [API 实现说明](../memoria/api/README.md)

## 1. 文档范围

本文描述 Memoria Agent `0.12.0` 本地 HTTP API（2026-10-09 更新分层记忆接口）。API 覆盖系统状态、会话、Agent 对话、个人分层记忆、共享记忆治理、工具调试、Tool Search、MCP、Drift 和运行追踪，不包含 Telegram、飞书、QQ 等外部通道。

共享记忆的数据流、权限边界和评测方法见 [记忆治理与多 Agent 共享记忆](./记忆治理与多Agent共享记忆.md)。

- 默认地址：`http://127.0.0.1:2237`
- API 前缀：`/api`
- Swagger UI：`/docs`
- OpenAPI JSON：`/openapi.json`
- 请求与响应：`application/json; charset=utf-8`

## 2. 通用协议

### 2.1 Request ID

客户端可发送 `X-Request-ID`。没有发送时，服务端会生成 32 位十六进制 ID。所有响应均返回同名 header；错误体也包含 `request_id`，方便对应日志和 trace。

### 2.2 可选认证与安全限制

配置 `[server.security].api_token` 后，除 `/api/health` 和独立认证的 `/api/shared/*` 外，API 与 `/metrics` 必须携带 `Authorization: Bearer <token>` 或 `X-API-Key`。`/api/shared/*` 始终要求 `X-Agent-Key`，并在存储层按 Agent 与空间角色鉴权；全局 API Token 不能代替 Agent key。治理管理员接口 `/api/governance/*` 使用全局 Token；未配置时仅允许本机回环连接。不安全方法带有 Origin 时必须匹配 `allowed_origins`。还可配置每 IP 每分钟限流和请求体上限。前端使用 `VITE_MEMORIA_API_TOKEN`，不要把真实值提交到 Git。

### 2.3 错误结构

```json
{
  "code": "validation_error",
  "message": "body.content: String should have at least 1 character",
  "request_id": "2d0dca2743664a58a7f3f974d6717042"
}
```

| 状态码 | code | 含义 |
|---|---|---|
| 404 | `not_found` | 会话、记忆或工具不存在 |
| 401/403 | `unauthorized` / `forbidden` | Token 或 Origin 不符合策略 |
| 409 | `conflict` | 会话正在运行，或写工具没有明确确认 |
| 413/429 | `payload_too_large` / `rate_limited` | 请求体或频率超过配置限制 |
| 422 | `validation_error` | 字段类型、长度、枚举或额外字段不合法 |
| 502 | `upstream_error` | 模型供应商调用失败 |

请求模型采用 `extra="forbid"`，未知字段会返回 422，避免拼写错误被静默忽略。

## 3. System API

### `GET /api/health`

最小存活检查，不访问模型。

```json
{"status":"ok","version":"0.12.0"}
```

### `GET /api/overview`

返回 Dashboard 所需聚合数据：会话、消息、记忆、trace 数量；脱敏模型配置；工具目录；Tool Search / MCP / Drift 状态；生命周期模块。模型配置只返回 `configured`，永不返回 API Key。

### `GET /metrics`

当 `metrics_enabled=true` 时返回 Prometheus 文本，包含 HTTP 请求、turn、token、后台任务和有效记忆指标。启用 API Token 后该路径也需要认证。

## 4. Session API

### `GET /api/sessions`

按更新时间倒序返回会话数组，每项包含 `message_count`。

### `POST /api/sessions`

请求：

```json
{"title":"Rust 学习计划"}
```

`title` 为 1–80 字。响应状态为 `201 Created`。

### `GET /api/sessions/{session_id}`

返回单个会话以及实时计算的消息数量。不存在返回 404。

### `PATCH /api/sessions/{session_id}`

请求 `{"title":"新标题"}`，用于重命名会话。

### `GET /api/sessions/{session_id}/messages?limit=100&anchor_id=`

不提供 `anchor_id` 时返回最近消息；提供该会话的消息 ID 时，返回该消息前后的上下文（含原消息），即使它不在最近 100 条中。`limit` 范围 1–1000；来源不属于会话或已删除时返回 404。

### `GET /api/messages/{message_id}/source`

按来源消息 ID 返回所属 `session_id`、会话标题、角色、原文和时间。原始消息或会话已删除时返回 404。前端据此进入会话，再用 `anchor_id` 定位并高亮原文。

### `DELETE /api/sessions/{session_id}`

删除会话并通过 SQLite 外键级联删除消息。成功返回 `204 No Content`。

## 5. Agent API

### `POST /api/sessions/{session_id}/chat`

请求：

```json
{"content":"请记住我偏好简洁回答，然后计算 27 * 3"}
```

执行顺序：保存用户消息、规划并按需执行 FTS/向量混合召回、拼装上下文、模型推理、工具循环、保存助手消息、排入后台记忆任务、记录 trace。

响应：

```json
{
  "message": {
    "id": "...",
    "session_id": "...",
    "role": "assistant",
    "content": "81。以后我会尽量简洁回答。",
    "created_at": "2026-07-22T10:00:00+00:00"
  },
  "memories_created": [],
  "trace": {
    "id": "...",
    "session_id": "...",
    "status": "completed",
    "steps": 2,
    "duration_ms": 920,
    "memories": [],
    "tools": [{"name":"calculate","ok":true,"elapsed_ms":0}],
    "error": null,
    "created_at": "2026-07-22T10:00:00+00:00"
  }
}
```

同一会话同一时间只允许一轮执行。第二个并发请求返回 409，防止消息顺序和工具上下文互相污染。不同会话可并发运行。

### `POST /api/sessions/{session_id}/chat/stream`

请求体与 `/chat` 相同，响应为 `text/event-stream`。事件类型包括 `phase`、`delta`、`tool`、`complete`、`error`、`cancelled`；`complete` 携带普通 `/chat` 的最终结构。客户端断开会取消上游 turn。

### `POST /api/sessions/{session_id}/cancel`

取消该会话正在运行的 turn，返回 `{"status":"cancelled|idle","session_id":"..."}`。

## 6. Memory API

### `GET /api/memories?q=&limit=100&status=active`

`status` 可为 `active`、`superseded` 或 `all`，默认只列出有效记忆。有效记忆在 `q` 非空时执行关键词与向量双路召回和 RRF 融合；已替代记忆只用于查询审计，不参与语义召回。`q` 最长 200 字，`limit` 范围 1–500。

### `POST /api/memories`

```json
{"content":"用户偏好简洁回答","kind":"preference","importance":4}
```

`kind` 可选：`fact`、`preference`、`profile`、`goal`、`procedure`；重要度为 1–5。

写入前先对同类型记忆做文本/向量候选预筛，再由 fast 模型在 `create`、`reinforce`、`supersede` 中选择：

- `create`：写入独立有效记忆。
- `reinforce`：已有记忆强化次数加一，不产生重复行。
- `supersede`：写入新记忆，旧记忆变为 `superseded` 并退出对话召回。

响应结构为 `{"action":"created|reinforced|superseded","memory":{...},"previous_id":null,"reason":"..."}`。其中 `memory` 包含 `status`、`reinforcement`、`supersedes_id`、`last_reinforced_at` 和可选的 `source_ref`，不会返回 embedding 原文。

### `POST /api/memories/reindex?limit=1000`

为没有向量的历史记忆批量回填 embedding，`limit` 范围 1–5000。响应示例：

```json
{"enabled":true,"indexed":36,"remaining":0}
```

embedding 没有完整配置时不会报错，返回 `enabled=false` 和剩余数量。

### `GET /api/memory-jobs?limit=50`

查询后台抽取任务，返回来源消息、状态、尝试次数、下次可用时间和租约信息。`completed` 表示提取已完成，候选仍需审核。

### `GET /api/memory-reviews?status=pending&limit=100`

返回自动提取的候选记忆。`status` 可为 `pending`、`approved`、`rejected`、`all`，默认只返回待审核项；`limit` 范围 1–500。每项带 `source_ref`（原始用户消息 ID）、候选正文、类型、重要度、审核状态和处理后的记忆 ID / 动作。待审核候选不参与对话召回。

### `POST /api/memory-reviews/{review_id}/approve`

批准前可修正候选，请求示例：

```json
{"content":"用户现在喜欢乌龙茶","kind":"preference","importance":4}
```

批准时走与手动写入相同的去重、强化和替代逻辑；用户明确批准可替代之前的纠正版本。响应 `status=approved` 并返回 `applied_memory_id`、`applied_action`（可能为 `created`、`reinforced`、`superseded` 或重复来源的 `skipped`）。空白或不合法字段返回 422；候选已处理返回 409；写入失败时恢复待审核并返回 502。重试和进程重启不会让同一候选重复入队。

### `POST /api/memory-reviews/{review_id}/reject`

拒绝待审核候选，不写入长期记忆；重复处理返回 409。审核队列记录保留，可通过 `status=all` 查看结果。

### `POST /api/memory-jobs/{job_id}/retry`

把 `failed` 任务清除错误并重新排队；其他状态返回 409。

### `POST /api/memories/undo`

请求 `{"source_refs":["用户消息 ID"],"dry_run":true}`。预览或执行撤销：停用这些来源创建的有效记忆，并恢复被它们 supersede 的旧版本。

### `GET /api/memories/{memory_id}/history`

查询一条记忆作为旧版本或新版本参与的替代记录，包括新旧 ID、正文快照、关系、判定原因和时间。记忆不存在时返回 404。

### `GET /api/memories/{memory_id}/timeline`

按创建时间返回与该记忆相连的完整替代链，包含当前与历史版本的正文、状态、来源、`source_ref`、`replacement_reason` 和 `replacement_relation`。用户纠正的关系值为 `correction`。仅在用户展开记忆详情时调用；最多返回 100 个版本，不返回 embedding。记忆不存在时返回 404。手动永久删除的版本不再作为记忆行返回。

### `POST /api/memories/{memory_id}/correct`

显式纠正当前有效记忆，并以原子事务写入新版本、旧版状态和带原因的替代记录。请求：

```json
{"content":"用户现在喜欢乌龙茶","kind":"preference","importance":4,"reason":"用户明确更正偏好"}
```

响应为新版本记忆，`source=user_correction`，`supersedes_id` 指向原版本。纠正内容或原因空白、字段不合法、内容与当前版本相同时返回 422；旧版本或并发失效返回 409；不存在返回 404。纠正后只有新版本参与检索和 `MEMORY.md` 投影。要恢复历史版本，可读取时间线，再用历史正文、类型和重要度调用此接口，仍会产生一条可审计的新版本。

### `PATCH /api/memories/{memory_id}`

可部分更新 `content`、`kind`、`importance`。从 `0.10.0` 起也会生成新版本，响应 ID 会变化，替代原因为“用户通过 PATCH 编辑”；推荐在 Dashboard 使用带显式原因的 `/correct`。至少应提供一个实际修改字段。

### `DELETE /api/memories/{memory_id}`

永久删除指定记忆，成功返回 204。

## 6.1 分层记忆与个人任务情景

这些接口使用全局 API Token，`X-Agent-Key` 不授予访问权限；未配置 Token 时沿用本地个人 API 模式。分层设计与配置解释见[中文 README](memory-layers/README.md)。当前没有单独的前端情景管理页，可通过本节接口或 `/docs` 管理。

| 方法 | 路径 | 参数与响应 |
| --- | --- | --- |
| GET | `/api/memory-layers` | 返回 `config` 的分层预算/开关和 `episodic` 统计；关闭时统计为 `{"enabled":false}` |
| GET | `/api/episodes?query=&status=active&limit=100` | `status=active/archive/all`、`limit=1..500`；有 query 时仅允许 active 相关性检索，结果还受 `episode_top_k` 限制；archive/all 无 query 时为管理列表，可看过期历史 |
| GET | `/api/episodes/{id}` | 管理详情，含来源消息与 trace ID，可回放过期/归档；来源已删除时 404 |
| PATCH | `/api/episodes/{id}` | `{"pin":true}` / `{"pin":false}` 固定或取消固定；或 `{"archive":true,"reason":"不再需要"}` 软归档 |
| POST | `/api/episodes/maintenance` | `{"dry_run":true}` 默认只预览；`false` 执行 TTL/容量软归档 |

关闭分层或情景模块时，情景列表/详情/操作返回 409。PATCH 只能执行一种动作，拒绝未知字段、数字/字符串布尔值、null、空请求或 `archive=false`，返回 422；归档不能通过 pin 恢复。来源追踪使用 `GET /api/messages/{user_message_id}/source`，再按 `session_id` 与 `anchor_id` 查询会话消息。

情景字段为 `id/session_id/user_message_id/assistant_message_id/task/outcome/result/tool_names/error/trace_id/status/pinned/created_at/expires_at/archived_at/archive_reason`。`outcome=completed/failed/cancelled` 是运行状态，completed 不代表任务成功已验证。管理接口回放不会改变召回状态；聊天和 `recall_episodes` 只返回活动且未过期或固定的相关记录。

维护响应包含 `dry_run/as_of/expired_ids/capacity_ids/candidate_ids/count/archived_count/pinned_protected/active_before/active_after`。dry-run 的 `active_after` 是预测值，`archived_count=0`；真实执行记录归档原因，不删除原始对话、语义事实或共享审计。默认 90 天 TTL、1000 条活动容量，固定记录受保护，固定数量超限时可超过容量。

```bash
curl -s http://127.0.0.1:2237/api/memory-layers
curl -s 'http://127.0.0.1:2237/api/episodes?status=all&limit=20'
curl -s -X POST http://127.0.0.1:2237/api/episodes/maintenance \
  -H 'Content-Type: application/json' -d '{"dry_run":true}'
```

启用认证时添加 `Authorization: Bearer <token>`。删除会话会清理来源消息、情景、摘要、抽取任务与审核候选；已批准个人语义记忆及来源操作账本仍保留，需要独立的删除/撤销操作。候选已不存在返回 404，候选存在但来源失效时批准返回 409。

## 7. Tool API

### `GET /api/tools`

返回工具名称、说明、风险级别、owner、always_on 与 search_hint。

### `GET /api/tools/search`

返回 Tool Search 状态：是否启用、直连工具、可搜索工具与分组。

### `POST /api/tools/search`

调试执行搜索：

```json
{"query":"记忆","top_k":5}
```

### `POST /api/tools/{tool_name}/execute`

用于 Dashboard 独立验证工具，不经过模型。

```json
{
  "arguments":{"expression":"(27+15)*3"},
  "confirm_write":false
}
```

只读工具可以直接执行。`write` 工具必须明确设置 `confirm_write=true`，否则返回 409。前端工具实验台默认只展示只读工具。

## 7.1 MCP API

### `GET /api/mcp`

返回 MCP 开关、配置路径、各 server 连接状态与已注册工具名。

### `POST /api/mcp/reload`

重新读取 `mcp_servers.json`、断开旧连接并重新注册工具。

## 7.2 Drift API

### `GET /api/drift`

返回调度状态（enabled、idle、预算、静默小时、最近 run）与最近运行列表。

### `POST /api/drift/run`

```json
{"force": false}
```

`force=true` 时跳过空闲/冷却/静默/日预算检查（仍受写工具白名单约束）。响应包含 `result` 与最新 `status`。

## 8. Trace API

### `GET /api/traces?session_id=&limit=50`

返回最新 trace。提供 `session_id` 时只查询该会话；`limit` 范围 1–200。trace 还包含 `metadata`，用于记录检索计划、上下文预算、模型耗时/重试/token usage。

Trace 用于诊断，不会自动加入模型上下文，也不会保存 API Key。

## 9. curl 示例

```bash
BASE=http://127.0.0.1:2237
SESSION=$(curl -s -X POST "$BASE/api/sessions" \
  -H 'Content-Type: application/json' \
  -d '{"title":"API 测试"}' | python -c 'import json,sys;print(json.load(sys.stdin)["id"])')

curl -s -X POST "$BASE/api/sessions/$SESSION/chat" \
  -H 'Content-Type: application/json' \
  -d '{"content":"用计算工具算 6*7"}'

curl -s "$BASE/api/traces?session_id=$SESSION"
```

## 10. 前端对应关系

| 前端功能 | 使用 API |
|---|---|
| 对话实验室 | sessions、messages、chat/stream、cancel |
| 长期记忆 | memories GET/POST/PATCH/DELETE/correct/timeline/reindex/undo、memory-reviews、memory-jobs、message source |
| 共享记忆治理 | governance/agents、shared/spaces、grants、proposals、memories、events |
| 运行追踪 | overview、traces |
| 工具实验台 | tools、tools execute |

生产部署时应在 `[server.security]` 启用 API Token、限定 `allowed_origins`、设置限流和请求体上限，并由反向代理补充 TLS 与访问日志脱敏。默认配置为了本地开发兼容仍不启用 Token，因此不能直接裸露到公网。

## 11. 记忆治理与多 Agent 共享 API

这是独立于旧个人记忆表的受控空间。`/api/governance/*` 是管理员接口；`/api/shared/*` 必须使用 `X-Agent-Key`。管理员创建 Agent 返回的 `token` **仅出现一次**，列表只含 `id/name/enabled/created_at`。禁用 Agent 后原 key 立即失效。

| 方法 | 路径 | 请求 / 说明 |
| --- | --- | --- |
| POST | `/api/governance/agents` | `{"name":"research-agent"}`，创建并一次返回 key。 |
| GET | `/api/governance/agents` | 管理员列出 Agent 元数据，不返回 key 或哈希。 |
| DELETE | `/api/governance/agents/{agent_id}` | 禁用 Agent key，成功 204。 |
| POST | `/api/governance/agents/{agent_id}/rotate-key` | 管理员轮换 key 并恢复启用，新 token 只返回一次、旧 token 立即失效，保留原有授权。 |
| POST | `/api/governance/spaces/{space_id}/import-memory/{memory_id}` | `{"actor_id":"owner-id","topic_key":"project:fact"}`，旧库有效记忆变为待审提案。 |
| POST / GET | `/api/shared/spaces` | 创建 `{"name":"研发","visibility":"shared|private"}` / 列出本 Agent 可见空间，返回 `access_role`。 |
| GET / POST | `/api/shared/spaces/{space_id}/grants` | owner/curator 查看授权；只有 owner 能通过 `{"agent_id":"...","role":"reader|contributor|curator"}` 授权。 |
| DELETE | `/api/shared/spaces/{space_id}/grants/{member_id}` | owner 撤销授权。私有空间不能授权。 |
| POST | `/api/shared/proposals` | 提交 `space_id/content/kind/importance/topic_key/source_type/source_ref/expires_at`；状态始终先为 `pending`。 |
| GET | `/api/shared/proposals?space_id=...&status=pending` | owner/curator 查看空间队列；contributor 只看自己提案；reader 无权访问。状态还可取 `approved/rejected/all`。 |
| POST | `/api/shared/proposals/{id}/approve` | owner/curator 发送 `{"reason":"已核对","expected_replaces_id":null}`。同主题当前有效版本的内容或类型、来源、有效期等元数据变化时，必须填当前有效记忆 ID；旧 ID 返回 409。 |
| POST | `/api/shared/proposals/{id}/reject` | owner/curator 发送 `{"reason":"证据不足"}`。 |
| GET | `/api/shared/memories?space_id=...&q=...&limit=100` | 只返回当前有读权限且已批准、未过期、未撤销的版本；省略 `space_id` 时搜索全部可见空间。 |
| GET | `/api/shared/memories/{id}` | 按空间 ACL 查询详情。普通成员按 ID 也无法读取已撤销或过期正文。 |
| GET | `/api/shared/memories/{id}/lineage` | 查询同空间同主题版本链；普通成员看不到已撤销/过期版本正文，curator/owner 可审计。 |
| POST | `/api/shared/memories/{id}/revoke` | owner/curator 发送 `{"reason":"不再适用"}`，同时撤销同空间同主题旧版本，普通成员不能通过旧 ID 找回正文；保留治理审计。 |
| GET | `/api/shared/events?space_id=...&limit=100` | 仅 owner/curator 可读的追加式治理事件。 |

提案列表返回实时 `conflict_memory_id` 和提交时的 `submitted_conflict_memory_id`，供审核界面标明变化。内容或来源、有效期等字段变化均要求版本 CAS；完全一致的提案才会 `confirm`。审批会重新验证提交者权限，旧记忆导入还会复验来源存在且有效，失效时返回 409。

记忆返回 `proposal_id`、`proposer_agent_id`、`approved_by`、`source_type`、`source_ref`、`version`、`supersedes_id` 和状态；详情与版本链还返回 `effective_status`，过期记忆显示为 `expired`。审批响应带 `action=activate|supersede|confirm`。`source_ref` 是来源指针，不会自动证明外部材料真实，也不授权读取旧个人会话。所有新请求体拒绝未知字段，审核与撤销理由必须包含非空白文字。

该层是单机、按 Agent key 与空间角色隔离的共享记忆服务。旧 `/api/memories`、聊天工具和来源消息 API 仍属于本地个人数据域，不应通过其路由读取共享层内容，也不能用它们证明多 Agent 隔离。
