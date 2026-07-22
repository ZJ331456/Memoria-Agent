# Tools 工具系统

## 组成

- `Tool`：名称、描述、JSON Schema、异步 executor、风险级别。
- `ToolRegistry`：注册、schema 输出、参数校验、超时执行和目录查询。
- `ToolResult`：统一返回工具名、成功状态、文本内容和耗时。
- `builtin.py`：不依赖外部通信软件的核心内置工具。

## 当前工具

| 工具 | 风险 | 功能 |
|---|---|---|
| `recall_memory` | read-only | 搜索长期记忆 |
| `memorize` | write | 明确写入记忆 |
| `forget_memory` | write | 按 ID 遗忘记忆 |
| `search_history` | read-only | 跨会话搜索历史消息 |
| `current_time` | read-only | 获取 IANA 时区时间 |
| `calculate` | read-only | AST 白名单算术计算 |

## 安全约束

工具参数先按 JSON Schema 的 required/type 做校验，每次执行默认最多 15 秒。计算器不使用 `eval`，只允许数字和白名单算术节点，并限制表达式长度与指数大小。

新增工具时应明确 `risk`。文件写入、Shell、网络 POST 等高副作用工具不能作为默认工具直接加入，必须先实现确认策略、作用域和审计。

## 工具循环保护

`ToolLoopGuard` 对一批 tool calls 生成稳定签名：工具名加按 key 排序后的 JSON 参数。相同签名最多真实执行两次；第三次会补齐被拒绝的 tool result，随后要求模型基于已有结果输出阶段性结论。这样既避免重复副作用和 token 浪费，也保持 OpenAI tool message 链协议完整。
