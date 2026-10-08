# Prompting：上下文帧与字符预算

[返回核心包](../README.md) · [运行时](../runtime/README.md) · [个人记忆](../memory/README.md)

## 文件与流程

| 文件 | 职责 |
|---|---|
| [assembler.py](assembler.py) | `PromptSection`、`PromptAssembler` 和带标记的 system-context-frame |
| [budget.py](budget.py) | `ContextBudget`，截短工具结果、保留近期消息并修复 tool call/result 配对 |
| [__init__.py](__init__.py) | 导出上述接口 |

[AgentRuntime](../runtime/agent.py) 先检索个人记忆并取得会话摘要，再调用 `PromptAssembler.assemble`。身份指令保留在首条 system message；记忆、摘要、SELF 文本、Skills 和工具目录等候选上下文按 section 顺序组成单独的低信任用户消息。帧内明确要求模型区分用户原话与系统提供的材料。随后追加最近会话历史。

每次模型调用前，`ContextBudget.apply` 以字符数裁剪消息：先截短超长工具结果，再优先保留首条 system 与最近消息，最后清理不完整的 assistant/tool 调用链。遇到模型明确报告上下文过长时，运行时以原预算的 45% 再试一次。裁剪计数进入 turn trace 的 `context_budget` metadata。

`[agent.context].char_budget` 在 [config.example.toml](../../config.example.toml) 中默认 60000；`memory_window` 控制读取的最近会话消息数。预算是字符近似，不是模型 tokenizer 的精确 token 计数；低信任上下文帧属于可被裁剪的消息。

## 从仓库根目录验证

```bash
python -m pytest -q tests/test_round7_akashic.py::test_prompt_assembler_builds_system_context_frame tests/test_core.py::test_context_budget_keeps_recent_and_valid_tool_protocol
```
