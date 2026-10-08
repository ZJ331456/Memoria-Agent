# Observability：追踪、事件与指标

[返回核心包](../README.md) · [运行时](../runtime/README.md) · [API](../api/README.md)

## 文件映射

| 文件 | 职责 |
|---|---|
| [context.py](context.py) | `RequestContext` 用 ContextVar 绑定 request/session/turn ID |
| [events.py](events.py) | 进程内 `EventBus`，订阅和派发 turn 事件 |
| [tracing.py](tracing.py) | `TurnTracer` 记录状态、步骤、耗时、召回 ID、工具链及 metadata |
| [metrics.py](metrics.py) | `MetricRegistry` 统计 HTTP 请求，生成 Prometheus 文本 |
| [../store.py](../store.py) | 持久化 turn trace 和运行计数 |

一轮对话由 [AgentRuntime](../runtime/agent.py) 创建 tracer，成功、失败和取消均写入不同状态的 trace；`GET /api/traces` 可按会话查询。工具记录包含参数、状态、耗时和结果预览；召回快照只存 ID、类型、重要度和来源，不复制记忆正文。写入前递归遮蔽常见密钥字段，并对文本中的 Bearer token 与 `sk-...` 模式脱敏。**这不是任意敏感数据的全面脱敏**：工具参数和错误文本仍需按使用场景控制访问与保留时间。

`EventBus` 派发 `turn.before`、`turn.after_step`、`turn.failed` 等事件；监听器异常会记录日志，不使 turn 失败。它仅在当前进程内运行，不是持久化消息队列。

API 中间件写入请求 ID 与 HTTP 计数；`GET /metrics` 根据进程计数和 Store 的统计生成文本指标。HTTP 计数随进程重启清零，trace 保存在 SQLite。`[observability].metrics_enabled` 控制指标端点是否开放；服务 Token 启用时端点受同一认证保护。

## 从仓库根目录验证

```bash
python -m pytest -q tests/test_core.py::test_trace_redacts_credentials tests/test_optimizations.py::test_trace_metadata_and_eval_metrics tests/test_round7_akashic.py::test_event_bus_and_request_context
```
