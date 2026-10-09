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
2. 增量压缩超出 `memory_window` 的历史；[MemoryQueryPlanner](../memory/planner.py) 决定是否召回个人事实，[EpisodicMemory](../memory/episodic.py) 召回相关任务经历。结果和摘要按各层上限进入 [PromptAssembler](../prompting/assembler.py) 的候选上下文帧。
3. 注入可用 Skills 和工具目录，追加最近消息，触发 `before_reasoning`。
4. 每次主聊天模型调用前执行 [ContextBudget](../prompting/budget.py)；模型返回工具调用时按 [ToolPolicy](../tools/policy.py) 的本轮授权执行，多个调用可并发。记忆/历史/技能读取共享本轮 [TurnMemoryBudget](../memory/budget.py)。结果以 tool message 回送模型，触发 `after_step`。
5. 获得最终回复后触发 `after_reasoning`，保存助手消息，投递自动提取任务，再触发 `after_turn` 并持久化 trace；以真实消息与 trace ID 记录简短情景。可选回调把模型增量与阶段/工具事件传给 SSE。

`SessionCompactor` 从最早未覆盖的消息逐批推进，用旧摘要加新消息更新，避免只压缩最近 500 条或覆盖早期约束。每次 ensure 最多 2 批、每批最多 80 条、输入最多 24000 字符；超长单消息用 `partial_message_id/partial_offset` 续读，完整读入后才标记 covered。游标与覆盖列表校验同会话真实前缀，来源删除或旧版错误覆盖会重建摘要。未出现新旧消息时直接复用，不反复调用模型。模型与回退摘要均最多 6000 字符，输入预算较小时自动下调，保证后续批次仍能推进；并发保存使用 CAS。原始消息仍在会话历史；两种摘要均可能丢失细节，不保证保留每条原话。覆盖列表校验与 JSON 重写成本随历史长度增长，尚未做长期大库的常数时间游标设计。

模型报告上下文过长时使用 45% 的紧急字符预算重试。工具批次重复第三次由 `ToolLoopGuard` 阻断，并在预算内请求模型结束本轮。trace 保存分层裁剪、读取配额、情景来源和技能版本，技能加载不被标记为任务成功。

## 配置与边界

`[agent].max_iterations` 控制工具步骤；值为 0 时仍受 **200 步硬上限**，正值也最多 200。`[agent.context]` 控制 `memory_window` 和 `char_budget`。`run(..., turn_kind="drift", skip_memory_enqueue=True)` 供 [Drift](../drift/README.md) 复用，另有有限步数和写工具白名单。

异常、取消分别记录 failed/cancelled trace 与短情景；`completed` 仅是执行结束状态。情景记录异常只写日志，不覆盖主响应；`turn_kind="drift"` 不录制情景。自动事实提取发生在后台，批准前不生效。此运行时没有 `agent_id/space_id` 参数；多 Agent 共享记忆由独立 [MemoryGovernance](../governance.py) 和 `/api/shared/*` 提供，当前不会自动注入此对话。

## 从仓库根目录验证

```bash
python -m pytest -q tests/test_round7_akashic.py tests/test_optimizations.py::test_sse_chat_endpoint_emits_delta_and_complete tests/test_core.py::test_builtin_tools_and_trace
python -m pytest -q tests/test_incremental_compaction.py tests/test_memory_layers_integration.py
```
