# Lifecycle：对话扩展阶段

[返回核心包](../README.md) · [运行时](../runtime/README.md) · [可观测性](../observability/README.md)

## 文件与接口

| 文件 | 职责 |
|---|---|
| [pipeline.py](pipeline.py) | `Phase`、`TurnContext`、`Pipeline.register/run/inspect` |
| [__init__.py](__init__.py) | 对外导出上述接口 |
| [../runtime/agent.py](../runtime/agent.py) | 在一轮对话中依次触发各阶段 |

`TurnContext` 保存本轮 `session_id`、用户输入、模型消息、召回记忆、工具调用链、回复和 metadata。`Pipeline.register(phase, handler, priority=100)` 注册异步处理器；数字小的先执行，同优先级保持注册顺序。`inspect()` 返回已注册处理器名称，供 `/api/overview` 展示。默认流水线没有预注册处理器，扩展方需要自行注册。

| 阶段 | 实际调用位置 |
|---|---|
| `before_turn` | 保存用户消息之前 |
| `before_reasoning` | 检索、组装 prompt 之后，首次模型调用之前 |
| `after_step` | 每批工具结果写回模型消息之后 |
| `after_reasoning` | 最终回复形成之后、保存助手消息之前 |
| `after_turn` | 保存助手消息并投递自动记忆任务之后、生成完成 trace 之前 |

处理器异常会传播到 [AgentRuntime](../runtime/README.md)，并产生 failed trace；该流水线不为处理器提供自动回滚。`Pipeline` 本身没有 TOML 开关，扩展行为由调用方注册决定。

## 从仓库根目录验证

```bash
python -m pytest -q tests/test_core.py::test_lifecycle_priority
```
