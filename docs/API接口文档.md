# Memoria Agent API 接口文档

## 1. 文档范围

本文描述 Memoria Agent `0.2.0` 本地 HTTP API。API 覆盖系统状态、会话、Agent 对话、长期记忆、工具调试和运行追踪，不包含 Telegram、飞书、QQ 等外部通道。

- 默认地址：`http://127.0.0.1:2237`
- API 前缀：`/api`
- Swagger UI：`/docs`
- OpenAPI JSON：`/openapi.json`
- 请求与响应：`application/json; charset=utf-8`

## 2. 通用协议

### 2.1 Request ID

客户端可发送 `X-Request-ID`。没有发送时，服务端会生成 32 位十六进制 ID。所有响应均返回同名 header；错误体也包含 `request_id`，方便对应日志和 trace。

### 2.2 错误结构

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
| 409 | `conflict` | 会话正在运行，或写工具没有明确确认 |
| 422 | `validation_error` | 字段类型、长度、枚举或额外字段不合法 |
| 502 | `upstream_error` | 模型供应商调用失败 |

请求模型采用 `extra="forbid"`，未知字段会返回 422，避免拼写错误被静默忽略。

## 3. System API

### `GET /api/health`

最小存活检查，不访问模型。

```json
{"status":"ok","version":"0.2.0"}
```

### `GET /api/overview`

返回 Dashboard 所需聚合数据：会话、消息、记忆、trace 数量；脱敏模型配置；工具目录；生命周期模块。模型配置只返回 `configured`，永不返回 API Key。

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

### `GET /api/sessions/{session_id}/messages?limit=100`

返回按时间正序排列的消息。`limit` 范围 1–1000。

### `DELETE /api/sessions/{session_id}`

删除会话并通过 SQLite 外键级联删除消息。成功返回 `204 No Content`。

## 5. Agent API

### `POST /api/sessions/{session_id}/chat`

请求：

```json
{"content":"请记住我偏好简洁回答，然后计算 27 * 3"}
```

执行顺序：保存用户消息、召回记忆、拼装上下文、模型推理、工具循环、保存助手消息、提取新记忆、记录 trace。

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

## 6. Memory API

### `GET /api/memories?q=&limit=100`

搜索记忆正文。`q` 最长 200 字，`limit` 范围 1–500。

### `POST /api/memories`

```json
{"content":"用户偏好简洁回答","kind":"preference","importance":4}
```

`kind` 可选：`fact`、`preference`、`profile`、`goal`、`procedure`；重要度为 1–5。

### `PATCH /api/memories/{memory_id}`

可部分更新 `content`、`kind`、`importance`。至少应提供一个字段。

### `DELETE /api/memories/{memory_id}`

永久删除指定记忆，成功返回 204。

## 7. Tool API

### `GET /api/tools`

返回工具名称、说明和风险级别。

### `POST /api/tools/{tool_name}/execute`

用于 Dashboard 独立验证工具，不经过模型。

```json
{
  "arguments":{"expression":"(27+15)*3"},
  "confirm_write":false
}
```

只读工具可以直接执行。`write` 工具必须明确设置 `confirm_write=true`，否则返回 409。前端工具实验台默认只展示只读工具。

## 8. Trace API

### `GET /api/traces?session_id=&limit=50`

返回最新 trace。提供 `session_id` 时只查询该会话；`limit` 范围 1–200。trace 包含召回记忆快照、工具参数与预览、步骤、耗时、状态和错误。

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
| 对话实验室 | sessions、messages、chat |
| 长期记忆 | memories GET/POST/DELETE |
| 运行追踪 | overview、traces |
| 工具实验台 | tools、tools execute |

生产部署前应增加身份认证、CSRF/Origin 策略、请求体大小限制和访问日志脱敏。目前定位仍是可信本机环境。
