---
name: drift-digest
description: 空闲 Drift 时审查 PENDING/SELF 与记忆冲突候选，产出可审计小结。适合无人值守整理。
triggers: drift, 空闲整理, pending, digest, 整理候选
metadata: {"memoria":{"emoji":"🌙","drift":true}}
---

# Drift Digest

无人值守整理技能：发现并记录，不擅自删除记忆。

## 流程

1. 用 `recall_memory` 抽查 1–2 个主题（偏好、目标、近期事实）
2. 必要时 `search_history` 核对对话里是否已有更新说法
3. 识别：仍正确 / 可能过时或冲突 / 琐碎可忽略
4. 高置信、用户曾明确表达的事实可用 `memorize`（仅当本轮写权限允许）
5. **禁止** `forget_memory`
6. 用简洁中文输出审计摘要（保留 / 建议更新 / 建议人工确认）

## 输出模板

```
## Drift 摘要
- 抽查主题：...
- 仍可靠：...
- 可疑/冲突：...
- 已写入记忆：...（无则写「无」）
- 建议用户确认：...
```
