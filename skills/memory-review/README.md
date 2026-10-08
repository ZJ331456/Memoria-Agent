# Memory Review：个人记忆审查

[技能目录](../README.md) · [运行时指令](SKILL.md) · [个人记忆引擎](../../memoria/memory/README.md)

用于抽查个人长期记忆中的过时信息、冲突与琐碎内容。触发词包括“整理记忆”“冲突”“记错”“遗忘”和 `memory review`。

## 工作流程

1. 用 `recall_memory` 按主题查找候选，必要时换同义问题。
2. 分类列出仍保留、建议更新和建议删除的内容。
3. 先给变更预览；明确确认后才建议执行 `memorize` / `forget_memory`，并解释原因。

`memorize` 可新建、强化或替代个人记忆；`forget_memory` 是旧库删除操作。它们不等同于共享治理的提案审批或可审计撤销。需要保留纠正原因与版本时，应使用工作台的“检查并纠正”；共享空间的治理应使用“共享治理”页面或 `/api/shared/*`。

确认步骤是技能指令，实际可执行工具受运行时 ToolPolicy 限制，不能将该技能视为独立的审核系统。

## 使用与验证

示例：“整理一下长期记忆中关于我的饮品偏好的冲突，先给修改建议。”

从仓库根目录运行：

```bash
python -m pytest -q tests/test_skills.py
python -m pytest -q tests/test_core.py -k 'user_correction or automatic_supersede'
```
