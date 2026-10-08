# Runtime：个人 Agent 对话编排

[返回核心包](../README.md) · [生命周期](../lifecycle/README.md) · [Prompting](../prompting/README.md) · [共享治理](../../docs/记忆治理与多Agent共享记忆.md)

## 文件映射

| 文件 | 职责 |
|---|---|
| [agent.py](agent.py) | `AgentRuntime.run`、工具调用循环、流式回调、取消和 trace |
| [compaction.py](compaction.py) | `SessionCompactor` 对较早消息生成并复用结构化摘要 |
| [__init__.py](__init__.py) | 导出运行时接口 |
| [../service.py](../service.py) | 构建运行时依赖，向 API 暴露聊天服务 |

## 一轮对话

1. 创建 `TurnContext` 与 `TurnTracer`，触发 `before_turn`，保存用户消息。
2. 压缩超出 `memory_window` 的历史；[MemoryQueryPlanner](../memory/planner.py) 决定是否召回个人记忆。[MemoryEngine](../memory/engine.py) 的结果和摘要进入 [PromptAssembler](../prompting/assembler.py) 的候选上下文帧。
3. 注入可用 Skills 和工具目录，追加最近消息，触发 `before_reasoning`。
4. 每次模型调用前执行 [ContextBudget](../prompting/budget.py)；模型返回工具调用时按 [ToolPolicy](../tools/policy.py) 的本轮授权执行，多个调用可并发。结果以 tool message 回送模型，触发 `after_step`。
5. 获得最终回复后触发 `after_reasoning`，保存助手消息，投递自动提取任务，再触发 `after_turn` 并持久化 trace。可选回调把模型增量与阶段/工具事件传给 SSE。

`SessionCompactor` 为同一批旧消息复用 SQLite 中的摘要；模型不可用时生成规则式回退摘要，原始消息仍在会话历史。模型报告上下文过长时使用 45% 的紧急字符预算重试。工具批次重复第三次由 `ToolLoopGuard` 阻断，并请求模型结束本轮。

## 配置与边界

`[agent].max_iterations` 控制工具步骤；值为 0 时仍受 **200 步硬上限**，正值也最多 200。`[agent.context]` 控制 `memory_window` 和 `char_budget`。`run(..., turn_kind="drift", skip_memory_enqueue=True)` 供 [Drift](../drift/README.md) 复用，另有有限步数和写工具白名单。

异常、取消分别记录 failed/cancelled trace。自动记忆提取发生在后台，批准前不生效。此运行时没有 `agent_id/space_id` 参数；多 Agent 共享记忆由独立 [MemoryGovernance](../governance.py) 和 `/api/shared/*` 提供，当前不会自动注入此对话。

## 从仓库根目录验证

```bash
python -m pytest -q tests/test_round7_akashic.py tests/test_optimizations.py::test_sse_chat_endpoint_emits_delta_and_complete tests/test_core.py::test_builtin_tools_and_trace
```
