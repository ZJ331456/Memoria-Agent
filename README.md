# Memoria Agent

可运行的个人 Agent：**对话 + 长期记忆 + 工具 / MCP + 空闲 Drift + 可观测 Dashboard**。

模型凭据写在被 Git 忽略的 `config.toml`（或 `data/models.override.toml`），HTTP API **永不回显密钥**，只返回是否已配置。

当前 API 版本：**0.9.0**（启动后见 `GET /api/health` 与 `/docs`）。

## 快速启动

```bash
cd Memoria-Agent
python -m venv .venv
.venv/bin/pip install -r requirements.txt
cd frontend && npm install && npm run build && cd ..
cp config.example.toml config.toml   # 填写 llm.*.api_key / base_url
.venv/bin/python main.py
```

浏览器打开 [http://127.0.0.1:2237](http://127.0.0.1:2237)。

| 场景 | 做法 |
|---|---|
| 前端热更新 | 另开终端 `cd frontend && npm run dev`（Vite 代理 `/api` → 2237） |
| 向量 KNN | `.venv/bin/pip install -r requirements-vector.txt`；未装则自动 JSON 扫描 |
| 自定义配置 | `python main.py --config /path/to/config.toml` |
| 默认数据库 | `data/memoria.db` |

改代码或升级依赖后请**重启** `main.py`，否则 Dashboard 可能仍是旧版本。

## 能力一览

### 对话与运行时

- 多会话 SQLite 持久化；OpenAI-compatible 主 / 快 / embedding 模型
- 多步工具循环、写权限策略、循环保护、超时与输出截断
- SSE 流式回复、停止生成、断连取消与 cancelled trace
- Prompt Context Frame、会话压缩摘要、EventBus、同一步并行工具

### 长期记忆

- 关键词 + 向量双路召回、RRF、FTS5、按类型限额注入
- 强化 / supersede / 后台 consolidation（租约、重试、撤销）
- Markdown 真双层：`MEMORY.md`（投影）/ `SELF.md`（可编辑注入）/ `PENDING.md`（候选缓冲）

### 工具扩展

- 内置：`recall_memory`、`memorize`、`forget_memory`、`search_history`、`current_time`、`calculate`、`load_skill`、`http_get`
- **Tool Search**：非 `always_on` 工具经 `tool_search` + `tool_call` 按需暴露，避免撑爆上下文（`[agent.tools].search_enabled`）
- **MCP**：stdio JSON-RPC，配置 `data/mcp_servers.json`（示例见根目录 `mcp_servers.example.json`）

### 空闲自动化

- **Drift**：无人对话时按预算跑限定技能（默认 `drift-digest` / `memory-review`）
- 静默小时、空闲阈值、日预算、写工具白名单；审计表 `drift_runs`
- 同技能读取上次已完成运行的摘要作为接续参考
- 配置：`[agent.drift]`（见 `config.example.toml`）

### Dashboard / 安全

- 对话、记忆、追踪（含 Drift）、工具实验台（含 MCP）、Setup 向导
- 可选 API Token、Origin、限流、请求体上限、Prometheus `/metrics`

## MCP（可选）

依赖：Node/`npx`，以及 [uv](https://docs.astral.sh/uv/) 的 `uvx`。

```bash
cp mcp_servers.example.json data/mcp_servers.json
# 把 filesystem / git 路径改成绝对路径后重启，或 POST /api/mcp/reload
```

| Server | 命令 | 用途 |
|---|---|---|
| filesystem | `npx -y @modelcontextprotocol/server-filesystem <沙箱目录>` | 沙箱读写 |
| fetch | `uvx mcp-server-fetch` | 网页转文本 |
| time | `uvx mcp-server-time` | 时区时间 |
| thinking | `npx -y @modelcontextprotocol/server-sequential-thinking` | 分步推理 |
| git | `uvx mcp-server-git --repository <repo>` | 只读 git（示例已限制工具） |

本地工具名形如 `mcp_{server}_{tool}`；默认走 Tool Search，不直连塞满 schema。

## Drift（可选）

在 `config.toml` 中：

```toml
[agent.drift]
enabled = true
min_idle_seconds = 300
interval_seconds = 10800
max_steps = 8
daily_budget = 6
quiet_hours = [0, 1, 2, 3, 4, 5, 6]
allowed_skills = ["drift-digest", "memory-review"]
allow_write_tools = ["memorize"]   # 不要放 forget_memory
timezone = "Asia/Shanghai"
```

- 状态 / 手动触发：`GET /api/drift`、`POST /api/drift/run`（`{"force": true}` 可跳过空闲/预算，仍不与用户对话并发）
- Dashboard「追踪」页可查看并手动跑一轮

## 文档索引

| 文档 | 内容 |
|---|---|
| [系统架构与实现说明.md](docs/系统架构与实现说明.md) | 模块边界与演进 |
| [API接口文档.md](docs/API接口文档.md) | 端点、错误协议、示例 |
| [项目规划说明书.md](docs/项目规划说明书.md) | 规划与对照 |
| [五项目对照与第十二轮优化.md](docs/五项目对照与第十二轮优化.md) | 五个参考项目对照、长问题召回修复与后续优先级 |
| [前后端优化与记忆时间线.md](docs/前后端优化与记忆时间线.md) | 前后端审计、界面改版及 Akashic/Claude-Mem 机制落地 |
| [简历项目经历.md](docs/简历项目经历.md) | 简历用描述 |
| [skills/README.md](skills/README.md) | 轻量 Skills 约定 |
| [memoria/api/README.md](memoria/api/README.md) | API 包维护说明 |

**分轮实现说明**

- [第五轮：九项落地](docs/核心优化审计-第五轮-九项落地.md) · [第六轮：生产化](docs/核心优化审计-第六轮-生产化九项.md)
- [第七轮：Akashic 运行时](docs/核心优化审计-第七轮-Akashic运行时提炼.md) · [第八轮：Skills](docs/核心优化审计-第八轮-轻量Skills.md)
- [第九轮：Markdown + Setup](docs/核心优化审计-第九轮-Markdown双层与Setup.md)
- [第十轮：Tool Search + MCP](docs/核心优化审计-第十轮-ToolSearch与MCP.md)
- [第十一轮：Drift](docs/核心优化审计-第十一轮-Drift空闲任务.md)

交互式 Swagger：服务启动后访问 `/docs`。

## 开发与验收

```bash
python -m pytest -q
cd frontend && npm run build
# 可选浏览器回归
cd frontend && npm run test:e2e
```

主要目录：`memoria/`（后端）、`frontend/`（Dashboard）、`skills/`（SKILL.md）、`data/`（本地运行时，默认不进 Git）、`docs/`（说明与审计）。
