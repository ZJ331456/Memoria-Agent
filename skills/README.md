# 内置技能说明

[项目首页](../README.md) · [技能加载实现](../memoria/skills/README.md) · [工具实现](../memoria/tools/README.md)

本目录保存个人 Agent 的操作说明书。运行时只扫描一级子目录中的 `SKILL.md`，按触发词选择正文注入 Context Frame，也可通过 `load_skill` 按名称读取。各子目录的 `README.md` 面向开发者，不参与技能加载。

## 技能导航

| 技能 | 示例请求 | 主要工具与边界 |
| --- | --- | --- |
| [summarize](summarize/README.md) | “总结这段对话” | `search_history`、`recall_memory`、`http_get`；只能总结实际获得的材料 |
| [weather](weather/README.md) | “北京今天的天气” | `http_get`；依赖外部天气服务及主机白名单 |
| [skill-creator](skill-creator/README.md) | “创建一个新技能” | 指导编写 SKILL.md；运行时没有 shell 或 write_file 工具 |
| [memory-review](memory-review/README.md) | “整理长期记忆中的冲突” | 检索后预览变更；确认后才建议调用写工具 |
| [drift-digest](drift-digest/README.md) | “空闲整理记忆” | Drift 抽查与摘要；禁止 `forget_memory`，写入受任务工具策略限制 |

summarize、weather、skill-creator 的流程参考 akashic-agent 并适配 Memoria 工具；memory-review、drift-digest 是本项目的记忆整理流程。它们调用的是个人记忆工具，目前没有通过 `agent_id + space_id` 接入共享治理层。

## 配置与加载

在仓库根目录的 `config.toml` 中配置：

```toml
[agent.skills]
enabled = true
directory = "skills"
max_inject = 2
```

相对目录以仓库根目录解析。`max_inject` 限制普通匹配技能数量，`always` 技能另外注入。目录在初始化时加载；修改后重启服务，或调用 `POST /api/skills/reload`。`GET /api/skills` 返回目录与可用性，`GET /api/skills/{name}` 返回单项正文；配置全局 API token 时须带相应认证。

## SKILL.md 约定

```markdown
---
name: skill-name
description: 一句话描述功能与触发场景
triggers: 触发词1, 触发词2, skill-name
always: false
metadata: {"memoria":{"requires":{"bins":[],"env":[]}}}
---

# 技能名称

操作步骤、真实工具和输出要求。
```

- 建议目录名与 `name` 一致，并显式填写中英文触发词。
- 当前 frontmatter 是轻量逐行解析，支持以上单行字段；`metadata` 必须是单行 JSON，不支持完整 YAML 嵌套语法。
- `requires.bins` 检查 PATH 中的可执行文件，`requires.env` 检查环境变量是否非空。依赖缺失会标记不可用，不自动安装依赖。
- 未提供 `triggers` 时从 description 提取关键词；并非向量或模型路由。
- 自动注入的单份正文默认最多 4000 字符；`load_skill` 可读取完整正文。保持说明简洁，避免把长参考材料当作每轮上下文。

技能是模型指令，不会注册新工具、执行脚本或构成权限沙箱。是否允许写入由运行时工具策略决定；自动对话提取的审核队列与技能调用 `memorize` 是不同路径。

## 添加和验证

1. 新建 `skills/<name>/SKILL.md`，声明明确触发词和真实依赖。
2. 增加同目录 README，解释用途、工具、限制和示例。
3. 从仓库根目录运行下面的测试；重载后在技能目录或 API 中检查名称、可用性与正文。

```bash
python -m pytest -q tests/test_skills.py
python -m pytest -q tests/test_round11_drift.py -k drift_digest_skill_present
```
