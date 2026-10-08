# MCP：外部 stdio 工具接入

[返回核心包](../README.md) · [工具系统](../tools/README.md) · [API](../api/README.md)

## 文件映射

| 文件 | 职责 |
|---|---|
| [config.py](config.py) | 从 JSON 读取、校验 server 配置与环境变量占位符 |
| [client.py](client.py) | 启动 stdio 子进程，用 JSON-RPC 完成 initialize、`tools/list`、`tools/call` |
| [host.py](host.py) | `McpHost` 连接/重载/停止 server，按 `mcp_{server}_{tool}` 注册到 ToolRegistry |
| [demo_server.py](demo_server.py) | 本地 echo/add 联调用最小 server |
| [__init__.py](__init__.py) | 导出 MCP 接口 |

`[agent.mcp].enabled` 控制是否启动，`config_file` 默认指向 `data/mcp_servers.json`；仓库提供 [mcp_servers.example.json](../../mcp_servers.example.json) 作为配置模板。文件不存在时不连接任何 server。每个 server 可配置 `name`、`command`、`cwd`、`env`、`enabled`、`allowed_tools`、`risk`、`timeout_seconds`。配置中的环境变量占位符从进程环境展开，状态接口仅公开环境变量名。

API lifespan 调用 [AgentService](../service.py) 启动/停止 `McpHost`，`GET /api/mcp` 查看状态，`POST /api/mcp/reload` 重新加载。host 先发现远端工具，再按允许名单过滤并注册；重载会注销旧工具。远端工具仍经 [ToolRegistry](../tools/README.md) 的参数校验、风险授权、超时与输出上限。风险级别是每个 server 的统一配置，设置为非只读后需要本轮明确授权；普通聊天的自动写授权只覆盖内置个人记忆工具。

当前客户端只接入 **MCP tools over stdio**，未实现通用 MCP resources、prompts 或 HTTP transport。外部 server 的进程与命令由本机配置决定；示例中占位路径需按实际目录填写。

## 从仓库根目录验证

```bash
python -m pytest -q tests/test_round10_tool_search_mcp.py
```
