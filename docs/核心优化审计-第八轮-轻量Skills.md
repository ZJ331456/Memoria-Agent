# 核心优化第八轮：轻量 Skills

本轮参考 `project_job/others/akashic-agent/skills` 与 `plugins/standard_tools/skill_catalog.py`，在 Memoria 内落地轻量技能目录，不引入插件热加载、资产归档或 Host Bridge。

## 能力

1. **目录发现**：扫描 `skills/*/SKILL.md`，解析 frontmatter（name/description/triggers/always/metadata）。
2. **触发匹配**：按触发词给用户消息打分，每轮最多注入 `max_inject` 个 Active Skills；目录摘要始终可见。
3. **工具**
   - `load_skill`：按名读取完整说明书
   - `http_get`：白名单主机只读拉取，服务 weather/summarize
4. **API**：`GET /api/skills`、`GET /api/skills/{name}`、`POST /api/skills/reload`
5. **内置技能**（改编自 Akashic + Memoria 原生）
   - `summarize`
   - `weather`
   - `skill-creator`
   - `memory-review`

## 配置

```toml
[agent.skills]
enabled = true
directory = "skills"
max_inject = 2

[agent.tools]
http_allowed_hosts = ["wttr.in", "api.open-meteo.com", "open-meteo.com"]
```

## 验收

```bash
python -m pytest -q tests/test_skills.py tests/test_round7_akashic.py
```
