# Drift：空闲后台轮次

[返回核心包](../README.md) · [运行时](../runtime/README.md) · [Skills](../skills/README.md) · [可观测性](../observability/README.md)

## 文件与调用流程

| 文件 | 职责 |
|---|---|
| [worker.py](worker.py) | `DriftWorker` 调度、状态查询、技能轮转、执行与结果记录 |
| [__init__.py](__init__.py) | 导出 `DriftWorker` |
| [../service.py](../service.py) | 装配 worker，API lifespan 启停轮询 |
| [../store.py](../store.py) | `drift_runs` 状态、预算统计和最近成功摘要 |

启用后，`run_loop` 周期性检查用户 turn 是否在运行、静默时段、距最近用户消息的空闲时间、冷却间隔、当天预算和上一轮状态。满足条件才从配置的技能名单中轮转一个，在独立的“〔Drift〕空闲整理”会话调用 [AgentRuntime.run](../runtime/agent.py)。同一技能上一轮成功摘要作为接续参考；运行记录保存步骤、摘要、trace ID 与结果状态。

Drift 将 `skip_memory_enqueue=True`，不触发普通聊天的自动提取任务；写工具只允许配置白名单，`forget_memory` 被硬阻断。若 Markdown 层开启，完成摘要可追加到 `PENDING.md`，它不是 SQLite 审核队列。`GET /api/drift` 查询状态和近期运行；`POST /api/drift/run` 手动触发。`force=true` 可越过常规时间和预算门槛，仍不会与活跃用户 turn 或未结束的上一轮重叠。

## 配置与边界

[config.example.toml](../../config.example.toml) 的 `[agent.drift]` 默认 `enabled=false`；可配置 `min_idle_seconds`、`interval_seconds`、`max_steps`、`daily_budget`、`quiet_hours`、`allowed_skills`、`allow_write_tools` 和 `timezone`。配置兼容旧 `[proactive.drift]` 字段。执行仍依赖模型与可用技能；Drift 不会自动向独立的 [多 Agent 共享记忆](../../docs/记忆治理与多Agent共享记忆.md)写提议。

## 从仓库根目录验证

```bash
python -m pytest -q tests/test_round11_drift.py
```
