# Observability 可观测性

## Turn Trace

每次对话都会生成一条 trace，字段包括：状态、session_id、步骤数、总耗时、召回记忆快照、工具调用链、错误和创建时间。trace 与聊天消息分表，便于排障时不污染用户上下文。

工具调用记录名称、参数、成功状态、耗时和最多 300 字结果预览。API 通过 `GET /api/traces` 提供最近 trace，也可使用 `session_id` 过滤。

## 安全说明

trace 不存模型 API Key。未来加入完整 prompt snapshot 时必须默认关闭或做敏感字段脱敏；生产环境还应设置保存期限和结果大小上限。

当前写入前会递归遮蔽 `api_key`、`authorization`、`password`、`secret`、`token` 等字段，并对文本中的 Bearer token 和 `sk-...` 模式脱敏。召回记忆只记录 ID、类型、重要度和来源，不复制记忆正文。
