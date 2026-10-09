# Prompting：上下文帧与字符预算

[返回核心包](../README.md) · [运行时](../runtime/README.md) · [个人记忆](../memory/README.md)

## 文件与流程

| 文件 | 职责 |
|---|---|
| [assembler.py](assembler.py) | `PromptSection`、`PromptAssembler` 和带标记的 system-context-frame |
| [budget.py](budget.py) | `ContextBudget`，截短工具结果、保留近期消息并修复 tool call/result 配对 |
| [__init__.py](__init__.py) | 导出上述接口 |

[AgentRuntime](../runtime/agent.py) 先检索个人事实和相关任务情景，取得增量会话摘要，再调用 `PromptAssembler.assemble`。身份指令保留在首条 system message；记忆、摘要、SELF 文本、Skills 和工具目录等候选上下文按 section 顺序组成单独的低信任用户消息。帧内明确要求模型区分用户原话与系统提供的材料。随后追加最近会话历史。

## 两级预算

`PromptAssembler(section_limits=..., max_frame_chars=...)` 先限制各分区，再均衡压缩整份帧，避免大段语义事实挤掉 Active Skills。分层默认预算为语义 3000、情景 1800、技能 2400、摘要 2000、目录合计 1200、SELF 1200 字符，整体帧 12000 字符。`section_stats` 记录每区原始/使用字符数和是否截断。

每次主聊天模型调用前（包括重复工具阻断后的最后调用），`ContextBudget.apply` 对消息正文、tool_calls 和每条 32 字符开销执行硬上限。保留完整首条 system、最新真实 user message 和 context frame；必要时均衡缩短帧，保留标题及首个来源标识，再选择近期完整消息。超过预算的用户输入只裁剪发给模型的副本，原文已先写入 SQLite。过大的系统指令无法完整放入时抛出错误，要求调整配置。工具调用和全部对应结果以完整组保留，避免孤立 tool message。

遇到模型明确报告上下文过长时，运行时以原预算的 45% 再试一次。trace 的 `context_budget` 记录原始/最终字符、丢弃/截短数量与是否保留帧；`memory_layers` 和 `memory_tool_budget` 记录分区装配及本轮读取配额。记忆读取的次数和字符限制由 [TurnMemoryBudget](../memory/budget.py) 另行执行，初始帧也计入读取字符额度。

`[agent.context].char_budget` 在 [config.example.toml](../../config.example.toml) 中默认 60000；`memory_window` 控制读取的最近会话消息数。`[memory.layers]` 控制分区上限。预算是字符近似，没有代替供应商 tokenizer，也没有保证输入加 `max_tokens` 必然适合每个模型的窗口；遇到不适配的模型需按实际窗口调小输入/输出配置。低信任帧仍需核对来源，保留它不意味着允许其中的指令覆盖系统规则。完整说明见[分层 README](../../docs/memory-layers/README.md)。

## 从仓库根目录验证

```bash
python -m pytest -q tests/test_round7_akashic.py::test_prompt_assembler_builds_system_context_frame tests/test_core.py::test_context_budget_keeps_recent_and_valid_tool_protocol
python -m pytest -q tests/test_layered_context_budget.py
```
