# Runtime 核心运行时

## 定位

`runtime` 是 Memoria 的用例编排层，对应参考项目里的 AgentCore、CoreRunner 和被动回复 loop。它不直接实现数据库、模型或工具，而是把这些能力按确定顺序组合成一轮完整对话。

## 一轮对话

1. 创建 `TurnContext` 和 `TurnTracer`。
2. 执行 `before_turn` 生命周期。
3. 保存用户消息并更新首次会话标题。
4. `MemoryQueryPlanner` 门控、改写 query 并选择类型，再由 `MemoryEngine` 召回相关长期记忆。
5. 组装系统提示、记忆和最近 `memory_window` 条消息。
6. 执行 `before_reasoning`。
7. 调用主模型；如果返回 tool calls，依次校验、执行并把结果追加为 tool message。
8. 每轮工具执行后运行 `after_step`，直到模型给出最终文本或达到 `max_iterations`。
9. 执行 `after_reasoning`，保存助手消息并写入持久化后台记忆任务。
10. 执行 `after_turn`，持久化 trace。

## 关键边界

- 最大工具迭代被限制在 1–20 之间，配置再大也不会无限循环。
- 工具失败会作为结构化结果返回模型，不会直接打断整轮。
- 记忆提取失败不影响用户已经得到的主回复。
- 任何未处理异常都会写入 failed trace，再交给 API 转换为 502。
- 每次模型调用前应用字符预算；供应商仍报告上下文超长时使用 45% 紧急预算重试一次。
- 相同工具调用批次最多执行两次，第三次由 loop guard 阻断并生成阶段性总结。
- 有事件回调时直接走模型 SSE；取消会贯穿模型、工具和 Runtime，并记录 cancelled trace。
- 写工具仅在用户表达明确记住/遗忘意图时授权。

## 扩展方式

新的推理策略应依赖 `Store`、`LLMClient`、`MemoryEngine`、`ToolRegistry` 接口，不要把 SQL 或 HTTP 写进 runtime。未来可增加并行只读工具，但应保持 `run(session_id, user_text, on_event)` 作为稳定入口。
