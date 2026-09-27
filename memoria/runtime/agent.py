from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from ..config import Settings
from ..lifecycle import Phase, Pipeline, TurnContext
from ..llm import ContextLengthError, LLMClient
from ..memory import MemoryEngine, MemoryQueryPlanner
from ..observability import EventBus, RequestContext, TurnTracer
from ..prompting import ContextBudget, PromptAssembler, PromptSection
from ..skills import SkillCatalog
from ..store import Store
from ..tools import ToolPolicy, ToolRegistry
from ..tools.loop_guard import ToolLoopGuard
from .compaction import SessionCompactor


class AgentRuntime:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        llm: LLMClient,
        memory: MemoryEngine,
        tools: ToolRegistry,
        pipeline: Pipeline | None = None,
        event_bus: EventBus | None = None,
        skills: SkillCatalog | None = None,
    ):
        self.settings = settings
        self.store = store
        self.llm = llm
        self.memory = memory
        self.tools = tools
        self.skills = skills
        self.pipeline = pipeline or Pipeline()
        self.event_bus = event_bus or EventBus()
        self.context_budget = ContextBudget(settings.context_char_budget)
        self.retrieval_planner = MemoryQueryPlanner(llm.plan_memory_retrieval)
        self.tool_policy = ToolPolicy()
        self.prompt_assembler = PromptAssembler()
        self.compactor = SessionCompactor(store, _FastSummarizer(llm))

    # Absolute ceiling even when max_iterations=0 (unlimited). Use /cancel to stop earlier.
    HARD_ITERATION_CAP = 200

    def _iteration_limit(self) -> int | None:
        value = int(self.settings.max_iterations)
        if value <= 0:
            return None
        return max(1, min(value, self.HARD_ITERATION_CAP))

    async def run(
        self,
        session_id: str,
        user_text: str,
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        request_id: str | None = None,
    ) -> tuple[dict, list[dict], dict]:
        turn_id = uuid.uuid4().hex
        tracer = TurnTracer(self.store, session_id)
        context = TurnContext(session_id, user_text)
        context.metadata["turn_id"] = turn_id
        with RequestContext(session_id=session_id, turn_id=turn_id, request_id=request_id):
            try:
                await self.pipeline.run(Phase.BEFORE_TURN, context)
                await self.event_bus.emit("turn.before", {"session_id": session_id, "turn_id": turn_id})
                if on_event:
                    await on_event({"type": "phase", "phase": "before_turn"})

                user_message = self.store.add_message(session_id, "user", user_text)
                session = self.store.session(session_id)
                if session and session["title"] == "新对话":
                    self.store.rename_session(session_id, user_text.replace("\n", " ")[:28])

                interrupt_note = (session or {}).get("interrupt_note") or ""
                compaction = await self.compactor.ensure(session_id, self.settings.memory_window)
                if compaction:
                    context.metadata["compaction"] = {
                        "reused": compaction.reused,
                        "covered": len(compaction.covered_message_ids),
                        "summary_chars": len(compaction.summary),
                    }

                history = self.store.messages(session_id, self.settings.memory_window)
                plan = await self.retrieval_planner.plan(user_text, history[:-1])
                context.metadata["retrieval_plan"] = plan.public_dict()
                context.memories = await self.memory.retrieve(plan.query, plan.limit, plan.kinds or None) if plan.needed else []
                context.metadata["retrieval"] = {
                    "injected": len(context.memories),
                    "top_score": context.memories[0].get("retrieval", {}).get("score") if context.memories else None,
                    "sufficient": bool(context.memories and context.memories[0].get("retrieval", {}).get("score", 0) > 0),
                }

                extra_sections: list[PromptSection] = []
                if self.skills:
                    matches = self.skills.select(user_text)
                    catalog_text, active_text = self.skills.render_sections(matches)
                    if catalog_text:
                        extra_sections.append(PromptSection("Available Skills", catalog_text, 40))
                    if active_text:
                        extra_sections.append(PromptSection("Active Skills", active_text, 45))
                    context.metadata["skills"] = {
                        "matched": [
                            {"name": item.skill.name, "score": item.score, "reason": item.reason}
                            for item in matches
                        ],
                        "catalog_size": len(self.skills.list()),
                    }

                assembled = self.prompt_assembler.assemble(
                    self.settings.system_prompt,
                    memories=context.memories,
                    compaction_summary=compaction.summary if compaction else "",
                    interrupt_note=interrupt_note,
                    extra_sections=extra_sections,
                )
                context.messages = assembled.as_messages()
                context.messages += [{"role": message["role"], "content": message["content"]} for message in history]
                context.metadata["prompt_sections"] = [section.name for section in assembled.sections]
                if interrupt_note:
                    self.store.clear_interrupt_note(session_id)

                await self.pipeline.run(Phase.BEFORE_REASONING, context)
                await self.event_bus.emit("turn.before_reasoning", {"session_id": session_id, "turn_id": turn_id})

                max_steps = self._iteration_limit()
                loop_guard = ToolLoopGuard(max_identical=2)
                authorization = self.tool_policy.authorize(user_text)
                context.metadata["tool_authorization"] = {
                    "allowed_write_tools": sorted(authorization.allowed_write_tools),
                    "reason": authorization.reason,
                }

                step = 0
                while True:
                    ceiling = max_steps if max_steps is not None else self.HARD_ITERATION_CAP
                    if step >= ceiling:
                        context.response = "工具调用达到安全上限，请缩小任务范围后重试。"
                        break
                    step += 1
                    budget = self.context_budget.apply(context.messages)
                    context.messages = budget.messages
                    context.metadata["context_budget"] = {
                        "original_chars": budget.original_chars,
                        "final_chars": budget.final_chars,
                        "dropped_messages": budget.dropped_messages,
                        "truncated_tool_results": budget.truncated_tool_results,
                    }
                    try:
                        if on_event:
                            async def stream_delta(text: str):
                                await on_event({"type": "delta", "content": text})

                            result = await self.llm.chat_stream(context.messages, tools=self.tools.schemas(), on_delta=stream_delta)
                        else:
                            result = await self.llm.chat(context.messages, tools=self.tools.schemas())
                    except ContextLengthError:
                        emergency = self.context_budget.emergency(context.messages)
                        context.messages = emergency.messages
                        context.metadata["context_budget"]["emergency_retry"] = True
                        context.metadata["context_budget"]["final_chars"] = emergency.final_chars
                        result = await self.llm.chat(context.messages, tools=self.tools.schemas())
                    self._record_llm(context, result)
                    if not result.tool_calls:
                        context.response = result.content or "我暂时没有生成有效回复，请再试一次。"
                        break

                    assistant = {
                        "role": "assistant",
                        "content": result.raw_message.get("content"),
                        "tool_calls": result.raw_message.get("tool_calls", []),
                    }
                    context.messages.append(assistant)
                    allowed, signature = loop_guard.check(result.tool_calls)
                    if not allowed:
                        for call in result.tool_calls:
                            context.messages.append({
                                "role": "tool",
                                "tool_call_id": call["id"],
                                "content": "工具循环保护：相同调用已连续执行两次，本次不再执行。请基于已有结果给出阶段性结论。",
                            })
                        context.tool_chain.append({
                            "step": step,
                            "name": "tool_loop_guard",
                            "arguments": {},
                            "ok": False,
                            "elapsed_ms": 0,
                            "preview": f"blocked repeated signature {signature[:160]}",
                        })
                        summary = await self.llm.chat(
                            context.messages
                            + [{"role": "user", "content": "请停止调用工具，基于已有结果直接给出简洁的最终答复；说明已经完成什么和仍缺少什么。"}]
                        )
                        context.response = summary.content or "检测到重复工具调用，已安全停止。"
                        break

                    tool_records = await self._execute_tools_parallel(
                        result.tool_calls,
                        authorization.allowed_write_tools,
                        step,
                        on_event,
                    )
                    context.tool_chain.extend(tool_records)
                    for call, record in zip(result.tool_calls, tool_records):
                        context.messages.append({
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": record["content"],
                        })
                    await self.pipeline.run(Phase.AFTER_STEP, context)
                    await self.event_bus.emit(
                        "turn.after_step",
                        {"session_id": session_id, "turn_id": turn_id, "step": step, "tools": len(tool_records)},
                    )

                await self.pipeline.run(Phase.AFTER_REASONING, context)
                assistant_message = self.store.add_message(session_id, "assistant", context.response)
                self.store.enqueue_memory_job(user_message["id"], user_text, context.response)
                created: list[dict] = []
                await self.pipeline.run(Phase.AFTER_TURN, context)
                await self.event_bus.emit(
                    "turn.after",
                    {"session_id": session_id, "turn_id": turn_id, "status": "completed", "steps": step},
                )
                trace = tracer.finish(
                    "completed",
                    max(1, len(context.tool_chain) + 1),
                    context.memories,
                    context.tool_chain,
                    metadata=context.metadata,
                )
                return assistant_message, created, trace
            except asyncio.CancelledError:
                note = f"上一轮在处理「{user_text[:80]}」时被中断；已完成 {len(context.tool_chain)} 个工具步骤。"
                self.store.set_interrupt_note(session_id, note)
                await self.event_bus.emit("turn.cancelled", {"session_id": session_id, "turn_id": turn_id})
                tracer.finish(
                    "cancelled",
                    len(context.tool_chain),
                    context.memories,
                    context.tool_chain,
                    "turn cancelled",
                    context.metadata,
                )
                raise
            except Exception as exc:
                await self.event_bus.emit(
                    "turn.failed",
                    {"session_id": session_id, "turn_id": turn_id, "error": f"{type(exc).__name__}: {exc}"},
                )
                tracer.finish(
                    "failed",
                    len(context.tool_chain),
                    context.memories,
                    context.tool_chain,
                    f"{type(exc).__name__}: {exc}",
                    context.metadata,
                )
                raise

    async def _execute_tools_parallel(
        self,
        tool_calls: list[dict[str, Any]],
        allowed_write_tools: set[str],
        step: int,
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> list[dict[str, Any]]:
        async def run_one(call: dict[str, Any]) -> dict[str, Any]:
            tool_result = await self.tools.execute(
                call["name"],
                call["arguments"],
                allowed_write_tools=allowed_write_tools,
            )
            record = {
                "step": step,
                "name": call["name"],
                "arguments": call["arguments"],
                "ok": tool_result.ok,
                "elapsed_ms": tool_result.elapsed_ms,
                "preview": tool_result.content[:300],
                "content": tool_result.content,
                "parallel": len(tool_calls) > 1,
            }
            if on_event:
                await on_event({"type": "tool", "tool": {key: value for key, value in record.items() if key != "content"}})
            return record

        if len(tool_calls) <= 1:
            return [await run_one(tool_calls[0])] if tool_calls else []
        return list(await asyncio.gather(*(run_one(call) for call in tool_calls)))

    @staticmethod
    def _record_llm(context: TurnContext, result) -> None:
        calls = context.metadata.setdefault("llm_calls", [])
        calls.append({"duration_ms": result.duration_ms, "retries": result.retries, "usage": result.usage})


class _FastSummarizer:
    """Prefer the cheap fast model for session compaction summaries."""

    def __init__(self, llm: LLMClient):
        self.llm = llm

    async def complete(self, messages: list[dict[str, str]], model=None, max_tokens: int | None = None) -> str:
        selected = model
        if selected is None and self.llm.settings.fast.model and self.llm.settings.fast.api_key:
            selected = self.llm.settings.fast
        return await self.llm.complete(messages, model=selected, max_tokens=max_tokens)
