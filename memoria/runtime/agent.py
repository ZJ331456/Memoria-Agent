from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from ..config import Settings
from ..lifecycle import Phase, Pipeline, TurnContext
from ..llm import LLMClient
from ..llm import ContextLengthError
from ..memory import MemoryEngine, MemoryQueryPlanner
from ..observability import TurnTracer
from ..prompting import ContextBudget
from ..store import Store
from ..tools import ToolRegistry
from ..tools.loop_guard import ToolLoopGuard


class AgentRuntime:
    def __init__(self, settings: Settings, store: Store, llm: LLMClient, memory: MemoryEngine, tools: ToolRegistry, pipeline: Pipeline | None = None):
        self.settings, self.store, self.llm, self.memory, self.tools = settings, store, llm, memory, tools
        self.pipeline = pipeline or Pipeline()
        self.context_budget = ContextBudget(settings.context_char_budget)
        self.retrieval_planner = MemoryQueryPlanner(llm.plan_memory_retrieval)

    async def run(self, session_id: str, user_text: str, on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None) -> tuple[dict, list[dict], dict]:
        tracer = TurnTracer(self.store, session_id)
        context = TurnContext(session_id, user_text)
        try:
            await self.pipeline.run(Phase.BEFORE_TURN, context)
            if on_event: await on_event({"type":"phase","phase":"before_turn"})
            user_message = self.store.add_message(session_id, "user", user_text)
            session = self.store.session(session_id)
            if session and session["title"] == "新对话": self.store.rename_session(session_id, user_text.replace("\n", " ")[:28])
            history = self.store.messages(session_id, self.settings.memory_window)
            plan = await self.retrieval_planner.plan(user_text, history[:-1])
            context.metadata["retrieval_plan"] = plan.public_dict()
            context.memories = await self.memory.retrieve(plan.query, plan.limit, plan.kinds or None) if plan.needed else []
            context.metadata["retrieval"] = {
                "injected": len(context.memories),
                "top_score": context.memories[0].get("retrieval", {}).get("score") if context.memories else None,
                "sufficient": bool(context.memories and context.memories[0].get("retrieval", {}).get("score", 0) > 0),
            }
            memory_text = "\n".join(f"- [{m['id']}] {m['content']}" for m in context.memories) or "（暂无相关长期记忆）"
            context.messages = [{"role":"system","content":self.settings.system_prompt+"\n\n你可以使用工具查找历史、管理记忆、计算和获取时间。相关长期记忆：\n"+memory_text}]
            context.messages += [{"role":m["role"],"content":m["content"]} for m in history]
            await self.pipeline.run(Phase.BEFORE_REASONING, context)
            max_steps = min(max(self.settings.max_iterations, 1), 20)
            loop_guard = ToolLoopGuard(max_identical=2)
            for step in range(max_steps):
                budget = self.context_budget.apply(context.messages)
                context.messages = budget.messages
                context.metadata["context_budget"] = {"original_chars":budget.original_chars,"final_chars":budget.final_chars,"dropped_messages":budget.dropped_messages,"truncated_tool_results":budget.truncated_tool_results}
                try:
                    if on_event:
                        async def stream_delta(text: str): await on_event({"type":"delta","content":text})
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
                assistant = {"role":"assistant","content":result.raw_message.get("content"),"tool_calls":result.raw_message.get("tool_calls",[])}
                context.messages.append(assistant)
                allowed, signature = loop_guard.check(result.tool_calls)
                if not allowed:
                    for call in result.tool_calls:
                        context.messages.append({"role":"tool","tool_call_id":call["id"],"content":"工具循环保护：相同调用已连续执行两次，本次不再执行。请基于已有结果给出阶段性结论。"})
                    context.tool_chain.append({"step":step+1,"name":"tool_loop_guard","arguments":{},"ok":False,"elapsed_ms":0,"preview":f"blocked repeated signature {signature[:160]}"})
                    summary = await self.llm.chat(context.messages + [{"role":"user","content":"请停止调用工具，基于已有结果直接给出简洁的最终答复；说明已经完成什么和仍缺少什么。"}])
                    context.response = summary.content or "检测到重复工具调用，已安全停止。"
                    break
                for call in result.tool_calls:
                    tool_result = await self.tools.execute(call["name"], call["arguments"], allow_write=self._allows_write(user_text))
                    record = {"step":step+1,"name":call["name"],"arguments":call["arguments"],"ok":tool_result.ok,"elapsed_ms":tool_result.elapsed_ms,"preview":tool_result.content[:300]}
                    context.tool_chain.append(record)
                    if on_event: await on_event({"type":"tool","tool":record})
                    context.messages.append({"role":"tool","tool_call_id":call["id"],"content":tool_result.content})
                await self.pipeline.run(Phase.AFTER_STEP, context)
            else: context.response = "工具调用达到安全上限，请缩小任务范围后重试。"
            await self.pipeline.run(Phase.AFTER_REASONING, context)
            assistant_message = self.store.add_message(session_id, "assistant", context.response)
            self.store.enqueue_memory_job(user_message["id"], user_text, context.response)
            created: list[dict] = []
            await self.pipeline.run(Phase.AFTER_TURN, context)
            trace = tracer.finish("completed", max(1,len(context.tool_chain)+1), context.memories, context.tool_chain, metadata=context.metadata)
            return assistant_message, created, trace
        except asyncio.CancelledError:
            tracer.finish("cancelled", len(context.tool_chain), context.memories, context.tool_chain, "turn cancelled", context.metadata)
            raise
        except Exception as exc:
            tracer.finish("failed", len(context.tool_chain), context.memories, context.tool_chain, f"{type(exc).__name__}: {exc}", context.metadata)
            raise

    @staticmethod
    def _record_llm(context: TurnContext, result) -> None:
        calls = context.metadata.setdefault("llm_calls", [])
        calls.append({"duration_ms":result.duration_ms,"retries":result.retries,"usage":result.usage})

    @staticmethod
    def _allows_write(user_text: str) -> bool:
        lowered = user_text.lower()
        return any(token in lowered for token in ("记住", "保存", "添加记忆", "忘掉", "删除记忆", "remember", "memorize", "forget"))
