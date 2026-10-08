> 历史记录：本文保留当时的审计与实现背景，功能现状和文档入口请以[当前文档导航](../README.md)为准。

# 核心优化第七轮：Akashic 运行时提炼

本轮继续参考 `/root/akashic-agent` 的核心运行时，但不接入飞书/Telegram/QQ、主动推送或完整插件热加载。目标是把对本地 Memoria 收益最高的六项能力落到现有 FastAPI + SQLite 闭环中。

## 1. Prompt Assembler / System Context Frame

参考 `agent/prompting/assembler.py`：身份 system prompt 与候选上下文分离。

- 新增 `memoria/prompting/assembler.py`
- 长期记忆、会话摘要、中断说明进入 `<system-reminder data-system-context-frame="true">` 帧
- 帧以 `role=user` 注入，并明确“系统提供、非用户陈述”，降低记忆被当成用户原话的风险

## 2. Session Compaction

参考 `plugins/compaction/message_summary.py` 的结构化摘要合同。

- 新增 `memoria/runtime/compaction.py` 与 `session_compactions` 表
- 历史超过 `memory_window` 时，用 fast 模型（失败则本地回退）生成固定标题摘要
- 按 covered message ids 幂等复用；trace metadata 记录是否复用与覆盖量
- API：`GET /api/sessions/{id}/compaction`

## 3. EventBus + Request ContextVars

参考 `bus/` 与 `core/error_context.py`。

- `EventBus`：进程内 fan-out，监听失败不影响主链路
- `RequestContext`：绑定 `session_id` / `turn_id` / `request_id`
- HTTP 中间件与 Agent turn 都会写入这些身份，便于日志关联

## 4. 同一步并行工具执行

同一步多个 tool_calls 使用 `asyncio.gather` 并行执行，并在 tool trace 中标记 `parallel`。

取消令牌仍通过 `CancelledError` 向上传播；循环保护与写授权逻辑保持不变。

## 5. 取消中断标记

参考 interrupt / soft-stop 思路：turn 被取消时写入 `sessions.interrupt_note`。

下一轮拼装 prompt 时注入 Interrupt 段，成功开始推理后清除，便于用户继续任务而不是从零猜测中断点。

## 6. Markdown 记忆导出 + 迭代上限修正

参考 Markdown 记忆层的可解释性：

- `GET /api/memories/export.md` 导出人类可读 `MEMORY.md`
- `max_iterations = 0` 表示不限制业务迭代（配置注释此前已写明）
- 仍保留硬顶 `200`，防止失控循环；长任务用 `/cancel` 中断

## 验收

```bash
cd /root/project_job/Memoria-Agent
python -m pytest -q tests/test_round7_akashic.py tests/test_optimizations.py
python -m compileall -q memoria
```

主动触达、外部通道和完整插件管理仍不在本轮范围；下一阶段可按真实使用再决定是否接入 inbound message bus。
