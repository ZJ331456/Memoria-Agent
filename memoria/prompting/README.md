# Prompting 与上下文预算

`ContextBudget` 防止会话历史和工具结果无限扩张。它采用确定性的字符预算，不依赖特定 tokenizer，因此可同时服务 DeepSeek、Qwen 和其他 OpenAI-compatible 模型。

处理顺序：先截断超长工具结果；始终保留第一条 system；从最新消息向前选择；最后删除缺少对应 assistant tool call 的孤立 tool message。模型明确返回上下文超长时，Runtime 使用 45% 紧急预算重试一次。

结果对象记录裁剪前后字符数、丢弃消息数和截断工具结果数，并写入 `TurnContext.metadata.context_budget`，便于 trace 和测试诊断。字符预算通过 `[agent.context].char_budget` 配置，默认 60000。

