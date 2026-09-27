# Memoria Skills

每个子目录放一份 `SKILL.md`。运行时会扫描本目录，按触发词匹配后把相关技能注入 Context Frame；模型也可调用 `load_skill` 按需读取。

格式参考 [akashic-agent skills](https://github.com/kachofugetsu09/akashic-agent)，已去掉对 Akashic 专属插件/CLI 的硬依赖，改为适配 Memoria 内置工具。

## 目录

| 技能 | 来源 | 用途 |
| --- | --- | --- |
| `summarize` | 改编自 akashic `summarize` | 总结链接、历史对话或长文本 |
| `weather` | 改编自 akashic `weather` | 查询天气（`http_get` + wttr.in / Open-Meteo） |
| `skill-creator` | 改编自 akashic `skill-creater` | 指导创建/改写本目录技能 |
| `memory-review` | Memoria 原生 | 审查、强化或清理长期记忆 |

## SKILL.md 约定

```markdown
---
name: skill-name
description: 一句话功能 + 触发场景
triggers: 触发词1, 触发词2, summarize
always: false
metadata: {"memoria":{"requires":{"bins":[],"env":[]}}}
---

# 标题
正文指令...
```

- `triggers`：中英文触发词，逗号分隔
- `always`：为 true 时每轮注入正文（请保持极短）
- `requires.bins/env`：缺失时标记不可用，但仍会出现在目录里
