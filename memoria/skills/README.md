# Skills：本地技能说明目录

[返回核心包](../README.md) · [工具系统](../tools/README.md) · [运行时](../runtime/README.md) · [内置技能目录](../../skills/README.md)

## 文件与发现规则

| 文件 | 职责 |
|---|---|
| [catalog.py](catalog.py) | `SkillCatalog` 发现、解析、可用性检查、触发词匹配和正文注入 |
| [__init__.py](__init__.py) | 导出目录接口 |
| [../runtime/agent.py](../runtime/agent.py) | 注入技能目录与本轮匹配的正文 |
| [../tools/builtin.py](../tools/builtin.py) | 常驻 `load_skill` 工具按名称读取技能 |

目录只扫描配置路径的**一级子目录中的 `SKILL.md`**，例如 [../../skills/weather/SKILL.md](../../skills/weather/SKILL.md)；不会把任意 Markdown 或 Python 文件当作技能。解析简易 YAML 风格 front matter 的 `name`、`description`、`triggers`、`always`，以及单行 JSON `metadata` 中的命令/环境变量依赖。缺少依赖的技能仍可列出，但不会自动匹配。

`SkillCatalog.select` 按 always、显式选择和触发词评分选择技能；`max_inject` 限制非 always 匹配数，正文每项最多注入 4000 字。运行时把目录摘要和匹配正文放进低信任 prompt 上下文；`load_skill` 返回可用技能的完整正文供模型查阅，**不会自动执行技能目录里的脚本**。`GET /api/skills`、`GET /api/skills/{name}`、`POST /api/skills/reload` 分别列出、读取和重扫目录。

`[agent.skills]` 在 [config.example.toml](../../config.example.toml) 中设置 `enabled`、`directory` 和 `max_inject`；默认目录为项目根的 `skills/`。内置目录目前包含 summarize、weather、skill-creator、memory-review 与 drift-digest；这些说明书不构成独立的多 Agent 权限主体，共享记忆仍走 [治理 API](../../docs/记忆治理与多Agent共享记忆.md)。

## 从仓库根目录验证

```bash
python -m pytest -q tests/test_skills.py
```
