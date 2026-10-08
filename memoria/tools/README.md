# Tools：工具注册、发现与执行

[返回核心包](../README.md) · [运行时](../runtime/README.md) · [MCP](../mcp/README.md) · [Skills](../skills/README.md)

## 文件映射

| 文件 | 职责 |
|---|---|
| [registry.py](registry.py) | `Tool` 元数据、`ToolRegistry` 注册/校验/限时执行、`ToolResult` |
| [builtin.py](builtin.py) | 个人记忆、会话搜索、时间、计算、技能加载和 HTTP GET 内置工具 |
| [search.py](search.py) | `ToolPresentation` 按需展示 schema，`tool_search` 与 `tool_call` 解码 |
| [policy.py](policy.py) | `ToolPolicy` 从用户输入授予本轮 `memorize/forget_memory` 写权限 |
| [loop_guard.py](loop_guard.py) | 对重复工具批次计算稳定签名并阻断第三次执行 |
| [http_get.py](http_get.py) | HTTP GET 初始主机白名单与输出截断 |

`build_registry` 注册 `recall_memory`、`memorize`、`forget_memory`、`search_history`、`current_time`、`calculate`、`load_skill`、`http_get`。开启 `[agent.tools].search_enabled` 后，模型直接看到 `always_on` 工具及 `tool_search/tool_call`；其他工具先搜索 schema，再通过 `tool_call` 执行。关闭后直接暴露全部普通工具 schema。外部 MCP 工具由 [McpHost](../mcp/README.md) 接入同一注册表。

`ToolRegistry.execute` 检查风险级别和本轮写授权，严格校验 JSON 参数，再执行 pre-hook 与异步 executor；结果统一为文本与耗时，默认输出上限 12000 字。取消异常向上传播，其他执行异常写入失败结果。`ToolPolicy` 只识别个人记忆的显式“记住/遗忘”意图；`memorize` 直接写个人记忆，**不向多 Agent 共享空间写提议**。共享记忆有独立的 Agent key、ACL 和审批流程。

`[agent.tools].http_allowed_hosts` 只检查请求的初始主机；`http_get` 使用 httpx 跟随重定向，因此不要把它视作完整的重定向目标隔离。`calculate` 解析 AST 并只允许基本算术。`load_skill` 返回 `SKILL.md` 正文，不执行技能目录里的脚本。

## 从仓库根目录验证

```bash
python -m pytest -q tests/test_optimizations.py::test_tool_permissions_hooks_validation_and_output_cap tests/test_core.py::test_tool_loop_guard_uses_stable_argument_signature tests/test_round10_tool_search_mcp.py::test_tool_search_presentation
```
