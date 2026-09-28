# 核心优化第十一轮：Drift 空闲任务

本轮是既定第 3 轮：在无人对话时按预算执行限定技能，做记忆整理类后台工作。

## 行为

- 后台循环：`DriftWorker.run_loop`（与 memory worker 并行）
- 触发条件（调度）：`enabled`、非用户 turn、非静默小时、空闲 ≥ `min_idle_seconds`、距上次 ≥ `interval_seconds`、未超 `daily_budget`
- 执行：专用会话「〔Drift〕空闲整理」→ 轮换 `allowed_skills` → 受限 `AgentRuntime.run`
- 写边界：仅 `allow_write_tools`（默认 `memorize`，永不默认开放 `forget_memory`）
- 不入队普通记忆抽取 job；摘要可 append 到 `PENDING.md`
- 审计表：`drift_runs`
- 同技能下一轮读取最近一次已完成运行的摘要（最多 800 字）作为接续参考；失败、取消及其他技能的摘要不注入

## 配置

`[agent.drift]`（兼容读取旧 `[proactive.drift]`）：

| 键 | 默认 | 含义 |
| --- | --- | --- |
| `enabled` | false | 总开关 |
| `min_idle_seconds` | 300 | 距最近用户消息 |
| `interval_seconds` | 10800 | 两次 Drift 最小间隔 |
| `max_steps` | 8 | 工具迭代上限 |
| `daily_budget` | 6 | 每日次数 |
| `quiet_hours` | 0–6 | 本地静默小时 |
| `allowed_skills` | drift-digest, memory-review | 候选技能 |
| `allow_write_tools` | memorize | 写工具白名单 |
| `timezone` | Asia/Shanghai | 静默/日预算时区 |

## API

- `GET /api/drift` — 状态 + 最近 runs
- `POST /api/drift/run` — `{"force": false|true}` 手动触发

Dashboard「追踪」页可查看状态并手动/强制跑一轮。

## 技能

新增 `skills/drift-digest/`：空闲审查记忆冲突候选，禁止 forget。

## 验收

```bash
python -m pytest -q
cd frontend && npm run build
```
