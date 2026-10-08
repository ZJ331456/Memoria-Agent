> 历史记录：本文保留当时的审计与实现背景，功能现状和文档入口请以[当前文档导航](../README.md)为准。

# 核心优化第十轮：Tool Search + MCP 客户端

本轮是既定第 2 轮能力：工具面变大时按需暴露 schema，并接入外部 MCP server。

## 1. Tool Search

- 配置：`[agent.tools].search_enabled`（默认 `true`，兼容别名 `tool_search_enabled`）
- 模块：`memoria/tools/search.py` 的 `ToolPresentation`
- 启用后模型看到的 schema = `always_on` 工具 + `tool_search` + `tool_call`
- 非直连工具（如 `memorize` / `http_get` / MCP）先 `tool_search`，再用 `tool_call({name, arguments})`
- Prompt 注入「可搜索工具目录」短描述；完整参数 schema 只在搜索命中后返回
- API：`GET/POST /api/tools/search`

默认 `always_on`：`recall_memory`、`current_time`、`calculate`、`load_skill`、`tool_search`。

## 2. MCP 客户端

- 配置：`[agent.mcp]` + `data/mcp_servers.json`（示例：`mcp_servers.example.json`）
- 模块：`memoria/mcp/{client,host,config,demo_server}.py`
- 协议：stdio JSON-RPC（`initialize` → `tools/list` → `tools/call`）
- 本地工具名：`mcp_{server}_{tool}`，默认 `always_on=false`，走 Tool Search
- 生命周期：应用启动连接、关闭断开；`POST /api/mcp/reload` 热重载
- 内置 demo：`python -m memoria.mcp.demo_server`（`echo` / `add`）

风险默认由 JSON 的 `risk` 控制；写风险工具仍受 `ToolPolicy` / `confirm_write` 约束。

## 3. Dashboard

工具页展示 Tool Search 指标、MCP server 状态表与「重载 MCP」按钮；工具目录增加 owner / 直连标记。

## 验收

```bash
python -m pytest -q
cd frontend && npm run build
```

复制示例配置启用 MCP：

```bash
cp mcp_servers.example.json data/mcp_servers.json
# 把 filesystem / git 的路径改成绝对路径；需要 uvx（Astral uv）和 npx
# 重启服务或 POST /api/mcp/reload
```

本机已验证的免 API Key 官方 server：`filesystem`、`fetch`、`time`、`sequential-thinking`、`git`（只读子集）。图谱记忆 `@modelcontextprotocol/server-memory` 在示例里默认关闭，避免与 Memoria 自有记忆混淆。
