# Drift Digest：空闲记忆小结

[技能目录](../README.md) · [运行时指令](SKILL.md) · [Drift 调度器](../../memoria/drift/README.md)

用于无人值守时抽查个人记忆并生成审计摘要。触发词包括 `drift`、`digest`、“空闲整理”和“整理候选”；默认配置将它列入 `[agent.drift].allowed_skills`，但 Drift 本身默认关闭。

## 工作流程

1. 用 `recall_memory` 抽查少量主题，必要时 `search_history` 核对过去说法。
2. 区分仍可靠、可疑冲突和琐碎内容，输出建议用户确认的事项。
3. 仅在本轮写工具授权允许时使用 `memorize`；禁止 `forget_memory`。

默认调度配置包含空闲阈值、静默时段、间隔、步数和每日预算。`allow_write_tools` 是额外的任务工具限制；技能指令本身不会给予写权限。标题提到 PENDING / SELF，但当前技能流程没有读取文件的专用工具，不能声称已检查这些文件正文。

Drift 是个人运行时中的后台任务，不是向外部通道发送通知，也未接入共享空间提案审核。

## 使用与验证

启用与调度规则见 Drift 模块 README。首次调试可先将 `allow_write_tools = []`，查看输出的小结与任务记录。

从仓库根目录运行：

```bash
python -m pytest -q tests/test_round11_drift.py
```

测试覆盖开关、跳过条件、任务记录、同技能续跑和技能存在性；不评估真实模型整理效果。
