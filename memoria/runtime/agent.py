from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from ..config import Settings
from ..lifecycle import Phase, Pipeline, TurnContext
from ..llm import ContextLengthError, LLMClient
from ..memory import MemoryEngine, MemoryQueryPlanner
from ..memory.budget import TurnMemoryBudget
from ..memory.episodic import EpisodicMemory
from ..observability import EventBus, RequestContext, TurnTracer
from ..prompting import ContextBudget, PromptAssembler, PromptSection
from ..memory.layer import MarkdownMemoryLayer
from ..skills import SkillCatalog, SkillMatch
from ..store import Store
from ..tools import ToolPolicy, ToolPresentation, ToolRegistry
from ..tools.loop_guard import ToolLoopGuard
from ..tools.policy import ToolAuthorization
from .compaction import SessionCompactor


logger = logging.getLogger(__name__)
_MEMORY_READ_TOOLS = frozenset({"recall_memory", "recall_episodes", "search_history", "load_skill"})


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
        markdown: MarkdownMemoryLayer | None = None,
        tool_presentation: ToolPresentation | None = None,
        episodes: EpisodicMemory | None = None,
    ):
        self.settings = settings
        self.store = store
        self.llm = llm
        self.memory = memory
        self.tools = tools
        self.skills = skills
        self.markdown = markdown
        self.tool_presentation = tool_presentation or ToolPresentation(tools, enabled=False)
        self.episodes = episodes
        self.layers = getattr(settings, "memory_layers", None)
        self.pipeline = pipeline or Pipeline()
        self.event_bus = event_bus or EventBus()
        self.context_budget = ContextBudget(settings.context_char_budget)
        self.retrieval_planner = MemoryQueryPlanner(llm.plan_memory_retrieval)
        self.tool_policy = ToolPolicy()
        if self.layers and self.layers.enabled:
            self.prompt_assembler = PromptAssembler(
                section_limits={
                    "Long-term Memory": self.layers.semantic_chars,
                    "Past Task Episodes": self.layers.episodic_chars,
                    "Active Skills": self.layers.procedural_chars,
                    "Session Summary": self.layers.summary_chars,
                    "Self": self.layers.self_chars,
                    "Available Skills": self.layers.catalog_chars,
                    "Tool Search": self.layers.catalog_chars,
                },
                max_frame_chars=min(self.layers.context_chars, self.layers.retrieval_max_chars),
            )
        else:
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
        *,
        authorization: ToolAuthorization | None = None,
        iteration_limit: int | None = None,
        skip_memory_enqueue: bool = False,
        force_skill: str | None = None,
        turn_kind: str = "chat",
    ) -> tuple[dict, list[dict], dict]:
        turn_id = uuid.uuid4().hex
        tracer = TurnTracer(self.store, session_id)
        context = TurnContext(session_id, user_text)
        context.metadata["turn_id"] = turn_id
        context.metadata["turn_kind"] = turn_kind
        user_message: dict[str, Any] | None = None
        memory_budget = (
            TurnMemoryBudget(self.layers.retrieval_max_calls, self.layers.retrieval_max_chars)
            if self.layers and self.layers.enabled else None
        )
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

                episodes = (
                    self.episodes.search(user_text, self.layers.episode_top_k)
                    if self.episodes and self.layers and len(user_text.strip()) >= 4 and turn_kind == "chat" else []
                )
                context.metadata["episode_retrieval"] = {
                    "matched": len(episodes), "injected": 0, "ids": [],
                }

                extra_sections: list[PromptSection] = []
                if episodes:
                    lines = []
                    for episode in episodes:
                        source = f"user={episode['user_message_id']}"
                        if episode.get("assistant_message_id"):
                            source += f", assistant={episode['assistant_message_id']}"
                        lines.append(
                            f"- [episode:{episode['id']}] 任务执行记录，不是已验证的用户事实 | source({source}) | "
                            f"outcome={episode['outcome']} | task={episode['task'][:200]} | "
                            f"result={episode['result'][:300]}"
                        )
                    extra_sections.append(PromptSection("Past Task Episodes", "\n".join(lines), 32))
                if turn_kind == "drift":
                    extra_sections.append(
                        PromptSection(
                            "Drift Mode",
                            "这是空闲 Drift 轮次：遵守技能与写权限白名单，完成后给出简洁审计摘要。",
                            12,
                        )
                    )
                if self.markdown and self.markdown.enabled:
                    self_text = self.markdown.self_excerpt()
                    if self_text.strip():
                        extra_sections.append(PromptSection("Self", self_text, 15))
                    context.metadata["markdown_layer"] = self.markdown.status()
                catalog_text = ""
                if self.skills:
                    if force_skill:
                        record = self.skills.get(force_skill)
                        if record:
                            matches = [SkillMatch(record, 100.0, "drift_forced")]
                        else:
                            matches = self.skills.select(force_skill)
                        catalog_text, active_text = self.skills.render_sections(matches)
                    else:
                        matches = self.skills.select(user_text)
                        catalog_text, active_text = self.skills.render_sections(matches)
                    if active_text:
                        extra_sections.append(PromptSection("Active Skills", active_text, 45))
                    context.metadata["skills"] = {
                        "matched": [
                            {"name": item.skill.name, "revision": item.skill.revision,
                             "score": item.score, "reason": item.reason}
                            for item in matches
                        ],
                        "catalog_size": len(self.skills.list()),
                        "loaded": [],
                    }
                tool_catalog = self.tool_presentation.catalog_prompt()
                if self.layers and self.layers.enabled:
                    catalog_cap = self.layers.catalog_chars
                    if catalog_text and tool_catalog:
                        skills_cap = catalog_cap - min(len(tool_catalog), catalog_cap // 2)
                        catalog_text = catalog_text[:skills_cap]
                        tool_catalog = tool_catalog[:catalog_cap - len(catalog_text)]
                    else:
                        catalog_text = catalog_text[:catalog_cap]
                        tool_catalog = tool_catalog[:catalog_cap]
                if catalog_text:
                    extra_sections.append(PromptSection("Available Skills", catalog_text, 40))
                if tool_catalog:
                    extra_sections.append(PromptSection("Tool Search", tool_catalog, 35))
                context.metadata["tool_search"] = self.tool_presentation.public_status()

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
                episode_content = next(
                    (section.content for section in assembled.sections if section.name == "Past Task Episodes"), ""
                )
                injected_episode_ids = [
                    item["id"] for item in episodes
                    if f"[episode:{item['id']}]" in episode_content and item["user_message_id"] in episode_content
                ]
                context.metadata["episode_retrieval"]["injected"] = len(injected_episode_ids)
                context.metadata["episode_retrieval"]["ids"] = injected_episode_ids
                if self.skills:
                    active_content = next(
                        (section.content for section in assembled.sections if section.name == "Active Skills"), ""
                    )
                    context.metadata["skills"]["injected"] = [
                        {"name": item.skill.name, "revision": item.skill.revision}
                        for item in matches
                        if re.search(r"(?m)^### skill:" + re.escape(item.skill.name) + r" \(", active_content)
                    ]
                context.metadata["memory_layers"] = {
                    "enabled": bool(self.layers and self.layers.enabled),
                    "section_stats": assembled.section_stats,
                    "frame_chars": len(assembled.context_frame["content"]) if assembled.context_frame else 0,
                }
                if memory_budget:
                    memory_budget.account_initial(context.metadata["memory_layers"]["frame_chars"])
                    context.metadata["memory_tool_budget"] = memory_budget.public_dict()
                if interrupt_note:
                    self.store.clear_interrupt_note(session_id)

                await self.pipeline.run(Phase.BEFORE_REASONING, context)
                await self.event_bus.emit("turn.before_reasoning", {"session_id": session_id, "turn_id": turn_id})

                if iteration_limit is not None:
                    max_steps = max(1, min(int(iteration_limit), self.HARD_ITERATION_CAP))
                else:
                    max_steps = self._iteration_limit()
                loop_guard = ToolLoopGuard(max_identical=2)
                if authorization is None:
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
                        "truncated_messages": budget.truncated_messages,
                        "preserved_context_frame": budget.preserved_context_frame,
                    }
                    try:
                        if on_event:
                            async def stream_delta(text: str):
                                await on_event({"type": "delta", "content": text})

                            result = await self.llm.chat_stream(
                                context.messages, tools=self.tool_presentation.schemas(), on_delta=stream_delta
                            )
                        else:
                            result = await self.llm.chat(context.messages, tools=self.tool_presentation.schemas())
                    except ContextLengthError:
                        emergency = self.context_budget.emergency(context.messages)
                        context.messages = emergency.messages
                        normal_stats = dict(context.metadata["context_budget"])
                        context.metadata["context_budget"] = {
                            "original_chars": normal_stats["original_chars"],
                            "final_chars": emergency.final_chars,
                            "dropped_messages": normal_stats["dropped_messages"] + emergency.dropped_messages,
                            "truncated_tool_results": normal_stats["truncated_tool_results"] + emergency.truncated_tool_results,
                            "truncated_messages": normal_stats["truncated_messages"] + emergency.truncated_messages,
                            "preserved_context_frame": emergency.preserved_context_frame,
                            "emergency_retry": True,
                            "normal": normal_stats,
                            "emergency": {
                                "original_chars": emergency.original_chars,
                                "final_chars": emergency.final_chars,
                                "dropped_messages": emergency.dropped_messages,
                                "truncated_tool_results": emergency.truncated_tool_results,
                                "truncated_messages": emergency.truncated_messages,
                                "preserved_context_frame": emergency.preserved_context_frame,
                            },
                        }
                        result = await self.llm.chat(context.messages, tools=self.tool_presentation.schemas())
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
                        final_messages = context.messages + [{
                            "role": "user",
                            "content": f"{user_text}\n\n请停止调用工具，基于已有结果直接给出简洁的最终答复；说明已经完成什么和仍缺少什么。",
                        }]
                        final_budget = self.context_budget.apply(final_messages)
                        context.metadata["context_budget"]["loop_guard_final_chars"] = final_budget.final_chars
                        summary = await self.llm.chat(final_budget.messages)
                        self._record_llm(context, summary)
                        context.response = summary.content or "检测到重复工具调用，已安全停止。"
                        break

                    tool_records = await self._execute_tools_parallel(
                        result.tool_calls,
                        authorization.allowed_write_tools,
                        step,
                        on_event,
                        memory_budget,
                    )
                    context.tool_chain.extend(tool_records)
                    if self.skills:
                        context.metadata["skills"]["loaded"].extend(
                            record["loaded_skill"] for record in tool_records if "loaded_skill" in record
                        )
                    if memory_budget:
                        context.metadata["memory_tool_budget"] = memory_budget.public_dict()
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
                if not skip_memory_enqueue:
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
                self._record_episode_safe(
                    turn_kind, session_id, user_message, assistant_message, user_text,
                    "completed", context.response, "", context.tool_chain, trace["id"],
                )
                return assistant_message, created, trace
            except asyncio.CancelledError:
                note = f"上一轮在处理「{user_text[:80]}」时被中断；已完成 {len(context.tool_chain)} 个工具步骤。"
                self.store.set_interrupt_note(session_id, note)
                await self.event_bus.emit("turn.cancelled", {"session_id": session_id, "turn_id": turn_id})
                trace = tracer.finish(
                    "cancelled",
                    len(context.tool_chain),
                    context.memories,
                    context.tool_chain,
                    "turn cancelled",
                    context.metadata,
                )
                self._record_episode_safe(
                    turn_kind, session_id, user_message, None, user_text,
                    "cancelled", "", "turn cancelled", context.tool_chain, trace["id"],
                )
                raise
            except Exception as exc:
                await self.event_bus.emit(
                    "turn.failed",
                    {"session_id": session_id, "turn_id": turn_id, "error": f"{type(exc).__name__}: {exc}"},
                )
                trace = tracer.finish(
                    "failed",
                    len(context.tool_chain),
                    context.memories,
                    context.tool_chain,
                    f"{type(exc).__name__}: {exc}",
                    context.metadata,
                )
                self._record_episode_safe(
                    turn_kind, session_id, user_message, None, user_text,
                    "failed", "", f"{type(exc).__name__}: {exc}", context.tool_chain, trace["id"],
                )
                raise

    async def _execute_tools_parallel(
        self,
        tool_calls: list[dict[str, Any]],
        allowed_write_tools: set[str],
        step: int,
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None,
        memory_budget: TurnMemoryBudget | None = None,
    ) -> list[dict[str, Any]]:
        async def run_one(call: dict[str, Any]) -> dict[str, Any]:
            decoded = self.tool_presentation.decode(call["name"], call["arguments"])
            if isinstance(decoded, str):
                record = {
                    "step": step,
                    "name": call["name"],
                    "arguments": call["arguments"],
                    "ok": False,
                    "elapsed_ms": 0,
                    "preview": decoded[:300],
                    "content": decoded,
                    "parallel": len(tool_calls) > 1,
                }
            else:
                tool_name, tool_args = decoded
                blocked = bool(memory_budget and not memory_budget.allow_call(tool_name))
                budget_blocked = blocked
                budget_truncated = False
                if blocked:
                    content = "[本轮记忆检索调用预算已用尽；工具未执行]"
                    tool_ok, elapsed_ms = False, 0
                else:
                    tool_result = await self.tools.execute(
                        tool_name,
                        tool_args,
                        allowed_write_tools=allowed_write_tools,
                    )
                    content = tool_result.content
                    tool_ok, elapsed_ms = tool_result.ok, tool_result.elapsed_ms
                    if memory_budget and tool_name in _MEMORY_READ_TOOLS:
                        consumed = memory_budget.consume(content)
                        if consumed != content:
                            if consumed.startswith("[本轮记忆检索预算已用尽"):
                                budget_blocked = True
                                tool_ok = False
                            else:
                                budget_truncated = True
                        content = consumed
                record = {
                    "step": step,
                    "name": tool_name,
                    "arguments": tool_args,
                    "ok": tool_ok,
                    "elapsed_ms": elapsed_ms,
                    "preview": content[:300],
                    "content": content,
                    "parallel": len(tool_calls) > 1,
                }
                if budget_blocked:
                    record["budget_blocked"] = True
                if budget_truncated:
                    record["budget_truncated"] = True
                if call["name"] == "tool_call":
                    record["via"] = "tool_call"
                if tool_name == "load_skill" and tool_ok and self.skills:
                    try:
                        loaded = json.loads(content)
                    except (TypeError, ValueError):
                        loaded = {}
                    if isinstance(loaded, dict) and loaded.get("instructions") and loaded.get("name"):
                        record["loaded_skill"] = {
                            "name": loaded["name"], "revision": loaded.get("revision", ""),
                        }
            if on_event:
                await on_event({"type": "tool", "tool": {key: value for key, value in record.items() if key != "content"}})
            return record

        if len(tool_calls) <= 1:
            return [await run_one(tool_calls[0])] if tool_calls else []
        return list(await asyncio.gather(*(run_one(call) for call in tool_calls)))

    def _record_episode_safe(
        self, turn_kind: str, session_id: str, user_message: dict[str, Any] | None,
        assistant_message: dict[str, Any] | None, task: str, outcome: str,
        result: str, error: str, tool_chain: list[dict[str, Any]], trace_id: str,
    ) -> None:
        if turn_kind != "chat" or not self.episodes or not user_message:
            return
        try:
            self.episodes.record(
                session_id=session_id,
                user_message_id=user_message["id"],
                assistant_message_id=assistant_message["id"] if assistant_message else None,
                task=task[:600], outcome=outcome, result=result[:1200], error=error[:300],
                tool_names=[item["name"] for item in tool_chain if self.tools.get(item.get("name", ""))],
                trace_id=trace_id,
            )
        except Exception:
            logger.exception("failed to record task episode for trace %s", trace_id)

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
