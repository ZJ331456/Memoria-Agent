# Tools 工具系统

## 组成

- `Tool`：名称、描述、JSON Schema、异步 executor、风险级别、owner、search_hint、always_on、独立超时和输出上限。
- `ToolRegistry`：注册/注销、schema 输出、严格参数校验、pre-hook、权限、超时执行和目录查询。
- `ToolResult`：统一返回工具名、成功状态、文本内容和耗时。
- `ToolPresentation`：Tool Search 呈现层（按需 schema + `tool_call` 解码）。
- `builtin.py`：不依赖外部通信软件的核心内置工具。

## 当前工具

| 工具 | 风险 | always_on | 功能 |
|---|---|---|---|
| `recall_memory` | read-only | yes | 搜索长期记忆 |
| `memorize` | write | no | 明确写入记忆 |
| `forget_memory` | write | no | 按 ID 遗忘记忆 |
| `search_history` | read-only | no | 跨会话搜索历史消息 |
| `current_time` | read-only | yes | 获取 IANA 时区时间 |
| `calculate` | read-only | yes | AST 白名单算术计算 |
| `load_skill` | read-only | yes | 加载技能说明书 |
| `http_get` | read-only | no | 白名单 HTTP 拉取 |
| `tool_search` | read-only | yes | 搜索可调用工具 schema |
| `mcp_*_*` | 可配 | no | 外部 MCP server 工具 |

## Tool Search

`[agent.tools].search_enabled=true` 时，模型只看到 always_on 工具、`tool_search` 和间接调用协议 `tool_call`。其余工具先搜索再调用，避免工具一多撑爆上下文。关闭后行为与旧版一致：全部 schema 直出。

## MCP

见 `memoria/mcp/` 与根目录 `mcp_servers.example.json`。本地工具名形如 `mcp_{server}_{remote}`。

## 安全约束

工具参数按 JSON Schema 的 required/type/additionalProperties 校验，每次执行默认最多 15 秒。非只读工具默认拒绝，必须获得明确授权；执行结果默认最多 12000 字，取消异常继续向上传播。计算器不使用 `eval`，只允许数字和白名单算术节点。

`ToolPolicy` 按用户原始表达授予最小能力：记住类意图只允许 `memorize`，遗忘类意图只允许 `forget_memory`。校验还覆盖 enum、字符串长度、数值范围、数组上限和嵌套字段；pre-hook 自身最多运行 5 秒。授权结果写入 trace metadata。

新增工具时应明确 `risk` 与 `always_on`。文件写入、Shell、网络 POST 等高副作用工具不能作为默认工具直接加入，必须先实现确认策略、作用域和审计。

## 工具循环保护

`ToolLoopGuard` 对一批 tool calls 生成稳定签名：工具名加按 key 排序后的 JSON 参数。相同签名最多真实执行两次；第三次会补齐被拒绝的 tool result，随后要求模型基于已有结果输出阶段性结论。这样既避免重复副作用和 token 浪费，也保持 OpenAI tool message 链协议完整。
