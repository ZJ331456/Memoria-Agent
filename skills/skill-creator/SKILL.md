---
name: skill-creator
description: 创建或改写 Memoria 技能 SKILL.md。触发词：新建技能, 创建 skill, 写技能, skill-creator, 改技能
triggers: 新建技能, 创建技能, 写技能, skill, SKILL.md, skill-creator, 改技能
metadata: {"memoria":{"emoji":"🛠️"}}
---

# Skill Creator（Memoria 适配）

改编自 akashic `skill-creater`。指导在本仓库 `skills/` 下新增或改写技能。

## 目录

```
skills/{skill-name}/
  SKILL.md          # 必须
  references/       # 可选长参考
```

## 必填 frontmatter

```markdown
---
name: skill-name
description: 功能 + 触发场景
triggers: 词1, 词2, 词3
always: false
metadata: {"memoria":{"requires":{"bins":[],"env":[]}}}
---
```

## 写作原则

- 正文控制在 100 行内；冗长内容放到 `references/`
- 触发词中英文都写
- 只写模型不知道的流程，优先示例命令/工具调用
- 依赖 Memoria 已有工具：`recall_memory`、`search_history`、`http_get`、`memorize` 等
- 不要假设存在 shell、write_file 或 Akashic 插件 API

## 交付检查

1. 目录名与 `name` 一致（小写连字符）
2. `description`/`triggers` 能被关键词匹配到
3. 若声明 `requires.bins/env`，说明缺失时的降级行为
