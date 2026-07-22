# Lifecycle 生命周期流水线

## 目标

生命周期让核心逻辑可以扩展而不修改 `AgentRuntime.run()`。设计参考 akashic-agent 的 PhaseModule，但当前采用更小的优先级注册模型，适合单机核心 MVP。

## 五个阶段

| Phase | 时机 | 典型用途 |
|---|---|---|
| `before_turn` | 消息写入前 | 输入规范化、安全过滤、指令识别 |
| `before_reasoning` | Prompt 完成后 | 增加上下文块、控制可见工具 |
| `after_step` | 每轮工具执行后 | 工具审计、结果裁剪、循环保护 |
| `after_reasoning` | 得到最终回答后 | 输出清洗、引用处理 |
| `after_turn` | 持久化收尾阶段 | 指标、后台任务投递 |

`TurnContext` 是阶段间唯一共享对象，包含 session、用户输入、模型消息、召回记忆、工具链、最终回答和 metadata。

## 注册与顺序

`Pipeline.register(phase, handler, priority)` 注册异步 handler。数字越小越早执行，相同数字维持注册顺序。`inspect()` 会返回当前模块表供 Dashboard 检查。

处理器应短小、可重入，不应静默吞异常。需要数据库事务的处理器应在自身内部完成提交或回滚。

