---
name: memory-review
description: 审查、整理、强化或清理长期记忆。触发词：整理记忆, 记忆审查, memory review, 冲突记忆, 删掉记错的
triggers: 整理记忆, 整理, 记忆审查, 审查记忆, memory review, 冲突记忆, 冲突, 记错, 遗忘, 清理记忆, 长期记忆
metadata: {"memoria":{"emoji":"🧠"}}
---

# Memory Review

Memoria 原生技能：帮助用户审查长期记忆质量。

## 流程

1. 用 `recall_memory` 按主题检索候选；必要时再换几个同义 query
2. 分类列出：
   - 仍正确且有用
   - 过时/冲突（建议 supersede 或删除）
   - 过于琐碎（可不保留）
3. 变更前先给预览；只有用户明确确认后才调用 `memorize` / `forget_memory`
4. 写操作说明原因；删除错误记忆时优先 `forget_memory`

## 输出模板

```
## 仍保留
- ...

## 建议更新
- 旧：...
  新：...

## 建议删除
- ...
```
