"""Offline, system-executed regression for the five local memory mechanisms.

Fixtures supply questions and expected outcomes. Every observed value comes from
Store, EpisodicMemory, MemoryEngine, SkillCatalog, or the real budget classes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import random
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from memoria.memory.budget import TurnMemoryBudget
from memoria.memory.engine import MemoryEngine
from memoria.memory.episodic import EpisodicMemory
from memoria.memory.layer_settings import MemoryLayerSettings
from memoria.prompting.assembler import PromptAssembler, PromptSection
from memoria.prompting.budget import ContextBudget
from memoria.skills.catalog import SkillCatalog
from memoria.store import Store


SEED = 331456
EPISODE_CLOCK = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _source(store: Store, task: str, title: str) -> tuple[str, str, str]:
    session = store.create_session(title)
    user = store.add_message(session["id"], "user", task)
    assistant = store.add_message(session["id"], "assistant", "任务完成")
    return session["id"], user["id"], assistant["id"]


def _check(
    checks: list[dict[str, Any]], name: str, layer: str,
    observed: Any, expected: Any, passed: bool,
) -> None:
    checks.append({
        "name": name, "layer": layer, "observed": observed,
        "expected": expected, "pass": bool(passed),
    })


def _labels(items: list[dict[str, Any]], mapping: dict[str, str]) -> list[str]:
    return [mapping.get(item["id"], "unknown") for item in items]


def run(seed: int = SEED) -> dict[str, Any]:
    rng = random.Random(seed)
    checks: list[dict[str, Any]] = []
    stores: list[Store] = []
    with tempfile.TemporaryDirectory(prefix="memoria-layers-eval-") as directory:
        root = Path(directory)

        def store_for(name: str) -> Store:
            store = Store(root / f"{name}.db", vector_backend="json")
            stores.append(store)
            return store

        try:
            # Episodic recall, source linkage, TTL, pin, and dry-run all use
            # actual persisted episodes and a fixed injectable test clock.
            store = store_for("episodes")
            clock = [EPISODE_CLOCK]
            settings = MemoryLayerSettings.from_dict({
                "episode_ttl_days": 1, "episode_top_k": 3, "episode_max_active": 10,
            })
            episodes = EpisodicMemory(store, settings, clock=lambda: clock[0])
            source_session, user_id, assistant_id = _source(store, "修复部署脚本路径错误", "部署")
            target = episodes.record(
                source_session, user_id, assistant_id,
                task="修复部署脚本路径错误", result="修正脚本路径后部署成功",
            )
            distractors = ["整理发票", "检查天气"]
            rng.shuffle(distractors)
            for index, topic in enumerate(distractors):
                sid, uid, aid = _source(store, f"{topic} {rng.randint(1000, 9999)}", f"干扰 {index}")
                episodes.record(sid, uid, aid, task=topic, result="归档完成")
            pinned_session, pinned_user, pinned_assistant = _source(store, "固定应急预案", "应急")
            pinned = episodes.record(
                pinned_session, pinned_user, pinned_assistant,
                task="固定应急预案", result="应急预案已核对",
            )
            episodes.pin(pinned["id"])
            current_session = store.create_session("当前问题")
            episode_labels = {target["id"]: "deployment", pinned["id"]: "pinned_plan"}
            found = episodes.search("部署脚本路径", exclude_session_id=current_session["id"])
            observed = _labels(found, episode_labels)
            _check(checks, "episode_cross_session_recall", "episodic", observed,
                   ["deployment"], observed == ["deployment"])
            unrelated = _labels(episodes.search("星际轨道引擎"), episode_labels)
            _check(checks, "episode_unrelated_abstention", "episodic", unrelated, [], not unrelated)
            resolved = store.message_source(found[0]["user_message_id"]) if found else None
            source_observed = {
                "message_matches": bool(resolved and resolved["message_id"] == user_id),
                "session_matches": bool(resolved and resolved["session_id"] == source_session),
                "role": resolved["role"] if resolved else None,
            }
            source_expected = {"message_matches": True, "session_matches": True, "role": "user"}
            _check(checks, "episode_source_resolution", "episodic",
                   source_observed, source_expected, source_observed == source_expected)

            clock[0] += timedelta(days=2)
            expired_hits = _labels(episodes.search("部署脚本路径"), episode_labels)
            _check(checks, "episode_ttl_filters_search", "forgetting", expired_hits, [], not expired_hits)
            pinned_hits = _labels(episodes.search("固定应急预案"), episode_labels)
            _check(checks, "episode_pin_survives_ttl", "forgetting", pinned_hits,
                   ["pinned_plan"], pinned_hits == ["pinned_plan"])
            stats_before = episodes.stats()
            events_before = len(episodes.events())
            preview = episodes.maintenance(dry_run=True)
            stats_after = episodes.stats()
            events_after = len(episodes.events())
            dry_observed = {
                "candidate_count": preview["count"],
                "archived_count": preview["archived_count"],
                "state_unchanged": stats_before == stats_after and events_before == events_after,
                "pinned_selected": pinned["id"] in preview["candidate_ids"],
            }
            dry_expected = {
                "candidate_count": 3, "archived_count": 0,
                "state_unchanged": True, "pinned_selected": False,
            }
            _check(checks, "forgetting_dry_run_has_no_effect", "forgetting",
                   dry_observed, dry_expected, dry_observed == dry_expected)

            # Capacity archiving is distinct from expiration and retains a
            # pinned oldest episode plus the newest unpinned episode.
            capacity_store = store_for("capacity")
            capacity_clock = [EPISODE_CLOCK]
            capacity = EpisodicMemory(
                capacity_store,
                MemoryLayerSettings.from_dict({"episode_max_active": 2}),
                clock=lambda: capacity_clock[0],
            )
            semantic_survivor = capacity_store.add_memory("用户偏好简短答案", "preference")
            capacity_ids: dict[str, str] = {}
            for index in range(4):
                sid, uid, aid = _source(capacity_store, f"容量任务 {index}", f"容量 {index}")
                item = capacity.record(sid, uid, aid, task=f"容量任务 {index}", result=f"完成 {index}")
                capacity_ids[item["id"]] = f"e{index}"
                capacity_clock[0] += timedelta(seconds=1)
            oldest = next(item_id for item_id, label in capacity_ids.items() if label == "e0")
            capacity.pin(oldest)
            applied = capacity.maintenance(dry_run=False)
            active = sorted(_labels(capacity.list(), capacity_ids))
            archived = sorted(_labels(capacity.list(status="archive"), capacity_ids))
            capacity_observed = {
                "active": active, "archived": archived,
                "archived_count": applied["archived_count"],
                "semantic_preserved": capacity_store.memory(semantic_survivor["id"])["status"] == "active",
            }
            capacity_expected = {
                "active": ["e0", "e3"], "archived": ["e1", "e2"],
                "archived_count": 2, "semantic_preserved": True,
            }
            _check(checks, "episode_capacity_archiving", "forgetting",
                   capacity_observed, capacity_expected, capacity_observed == capacity_expected)

            source_store = store_for("source_delete")
            source_episodes = EpisodicMemory(source_store, MemoryLayerSettings())
            sid, uid, aid = _source(source_store, "检查项目缓存", "待删除来源")
            doomed = source_episodes.record(sid, uid, aid, task="检查项目缓存")
            deleted = source_store.delete_session(sid)
            source_delete_observed = {
                "deleted": deleted,
                "detail_missing": source_episodes.detail(doomed["id"]) is None,
                "search_empty": source_episodes.search("项目缓存") == [],
                "source_delete_event": any(
                    event["action"] == "source_delete" for event in source_episodes.events(doomed["id"])
                ),
            }
            source_delete_expected = dict.fromkeys(source_delete_observed, True)
            _check(checks, "source_session_deletion_no_episode_leak", "episodic",
                   source_delete_observed, source_delete_expected,
                   source_delete_observed == source_delete_expected)

            # A real pending review is stored separately from active memories.
            semantic_store = store_for("semantic_prompt")
            sid, uid, aid = _source(semantic_store, "我偏好乌龙茶", "审核")
            job = semantic_store.enqueue_memory_job(uid, "我偏好乌龙茶", "已收到")
            claimed = semantic_store.claim_memory_job("eval-worker")
            if not claimed or claimed["id"] != job["id"]:
                raise RuntimeError("real memory job could not be claimed")
            review = semantic_store.stage_memory_reviews(job["id"], "eval-worker", [
                {"content": "用户偏好乌龙茶", "kind": "preference", "importance": 3}
            ])[0]
            engine = MemoryEngine(semantic_store)
            retrieved_pending = asyncio.run(engine.retrieve("乌龙茶", limit=3))
            pending_prompt = PromptAssembler().assemble("系统规则", memories=retrieved_pending)
            pending_observed = {
                "review_status": semantic_store.memory_review(review["id"])["status"],
                "retrieved_count": len(retrieved_pending),
                "pending_in_frame": bool(
                    pending_prompt.context_frame and "乌龙茶" in pending_prompt.context_frame["content"]
                ),
            }
            pending_expected = {
                "review_status": "pending", "retrieved_count": 0, "pending_in_frame": False,
            }
            _check(checks, "semantic_pending_review_not_injected", "semantic",
                   pending_observed, pending_expected, pending_observed == pending_expected)

            # Skill content and revision are read from actual SKILL.md files.
            skill_dir = root / "skills" / "deploy"
            skill_dir.mkdir(parents=True)
            skill_file = skill_dir / "SKILL.md"
            skill_file.write_text(
                "---\nname: deploy\ndescription: 部署步骤\ntriggers: 部署\n---\n# Deploy\n步骤 v1\n",
                encoding="utf-8",
            )
            catalog = SkillCatalog(root / "skills")
            revision_before = catalog.get("deploy").revision
            skill_file.write_text(
                "---\nname: deploy\ndescription: 部署步骤\ntriggers: 部署\n---\n# Deploy\n步骤 v2：核对发布路径\n",
                encoding="utf-8",
            )
            catalog.reload()
            selected = catalog.select("请部署发布手册")
            _, active_skill_text = catalog.render_sections(selected)
            skill_observed = {
                "revision_changed": catalog.get("deploy").revision != revision_before,
                "selected": [match.skill.name for match in selected],
                "updated_body_visible": "步骤 v2" in active_skill_text,
                "revision_before": revision_before,
                "revision_after": catalog.get("deploy").revision,
            }
            skill_expected = {
                "revision_changed": True, "selected": ["deploy"], "updated_body_visible": True,
            }
            _check(checks, "skill_revision_reload_visible", "procedural",
                   skill_observed, skill_expected,
                   all(skill_observed[key] == value for key, value in skill_expected.items()))

            long_memory = semantic_store.add_memory("部署发布手册 " + "甲" * 9000, "procedure")
            retrieved = asyncio.run(engine.retrieve("部署发布手册", limit=3))
            assembled = PromptAssembler(
                section_limits={"Long-term Memory": 3000, "Active Skills": 3000},
                max_frame_chars=700,
            ).assemble(
                "系统规则", memories=retrieved,
                extra_sections=[PromptSection("Active Skills", active_skill_text, 45)],
            )
            frame = assembled.context_frame["content"] if assembled.context_frame else ""
            section_observed = {
                "semantic_retrieved": long_memory["id"] in {item["id"] for item in retrieved},
                "memory_id_retained": f"[{long_memory['id']}]" in frame,
                "active_skill_retained": "### skill:deploy" in frame,
                "frame_chars": len(frame),
            }
            section_expected = {
                "semantic_retrieved": True, "memory_id_retained": True,
                "active_skill_retained": True, "frame_chars_at_most": 700,
            }
            _check(checks, "long_semantic_does_not_evict_active_skill", "working",
                   section_observed, section_expected,
                   all(section_observed[key] for key in (
                       "semantic_retrieved", "memory_id_retained", "active_skill_retained"
                   )) and len(frame) <= 700)

            raw_user = "旧背景" * 5000 + "请回答最新问题"
            raw_user_chars = len(raw_user)
            prompt_input = [
                assembled.system_message, assembled.context_frame,
                {"role": "user", "content": raw_user},
            ]
            prompt_result = ContextBudget(max_chars=2000).apply(prompt_input)
            sent_frame = next((item["content"] for item in prompt_result.messages
                               if "data-system-context-frame" in str(item.get("content", ""))), "")
            cap_observed = {
                "final_chars": prompt_result.final_chars,
                "frame_retained": bool(sent_frame),
                "active_skill_retained": "## Active Skills" in sent_frame,
                "latest_user_tail_retained": prompt_result.messages[-1]["content"].endswith("请回答最新问题"),
                "raw_user_unchanged": prompt_input[-1]["content"] == raw_user
                and len(prompt_input[-1]["content"]) == raw_user_chars,
            }
            cap_expected = {
                "final_chars_at_most": 2000, "frame_retained": True,
                "active_skill_retained": True, "latest_user_tail_retained": True,
                "raw_user_unchanged": True,
            }
            _check(checks, "prompt_cap_preserves_frame_and_latest_user", "working",
                   cap_observed, cap_expected,
                   cap_observed["final_chars"] <= 2000 and all(cap_observed[key] for key in (
                       "frame_retained", "active_skill_retained", "latest_user_tail_retained",
                       "raw_user_unchanged",
                   )))

            calls = [
                {"id": "call-a", "type": "function", "function": {"name": "first", "arguments": "{}"}},
                {"id": "call-b", "type": "function", "function": {"name": "second", "arguments": "{}"}},
            ]
            protocol_result = ContextBudget(max_chars=3000, tool_result_chars=500).apply([
                assembled.system_message, assembled.context_frame,
                {"role": "tool", "tool_call_id": "orphan", "content": "不应保留"},
                {"role": "user", "content": "检查部署工具"},
                {"role": "assistant", "content": None, "tool_calls": calls},
                {"role": "tool", "tool_call_id": "call-a", "content": "A" * 300},
                {"role": "tool", "tool_call_id": "call-b", "content": "B" * 300},
                {"role": "user", "content": "请基于工具结果回答"},
            ])
            kept_calls = sorted(call["id"] for message in protocol_result.messages
                                for call in message.get("tool_calls", []))
            kept_tools = sorted(message["tool_call_id"] for message in protocol_result.messages
                                if message.get("role") == "tool")
            protocol_observed = {
                "call_ids": kept_calls, "tool_ids": kept_tools,
                "orphan_absent": "orphan" not in kept_tools,
                "final_chars": protocol_result.final_chars,
            }
            protocol_expected = {
                "call_ids": ["call-a", "call-b"], "tool_ids": ["call-a", "call-b"],
                "orphan_absent": True, "final_chars_at_most": 3000,
            }
            _check(checks, "prompt_tool_protocol_complete", "working",
                   protocol_observed, protocol_expected,
                   kept_calls == kept_tools == ["call-a", "call-b"]
                   and protocol_observed["orphan_absent"] and protocol_result.final_chars <= 3000)

            call_budget = TurnMemoryBudget(max_calls=2, max_chars=2000)
            call_budget.account_initial(len(frame))
            allowed = []
            for name in ("recall_memory", "recall_episodes", "search_history"):
                accepted = call_budget.allow_call(name)
                allowed.append(accepted)
                if accepted:
                    call_budget.consume("记忆" * 30)
            call_state = call_budget.public_dict()
            calls_observed = {
                "allowed": allowed, "calls": call_state["calls"],
                "blocked_calls": call_state["blocked_calls"],
                "initial_frame_accounted": call_state["used_chars"] >= len(frame),
            }
            calls_expected = {
                "allowed": [True, True, False], "calls": 2,
                "blocked_calls": 1, "initial_frame_accounted": True,
            }
            _check(checks, "turn_memory_call_cap_includes_initial_frame", "working",
                   calls_observed, calls_expected, calls_observed == calls_expected)

            char_limit = len(frame) + 100
            char_budget = TurnMemoryBudget(max_calls=6, max_chars=char_limit)
            char_budget.account_initial(len(frame))
            first_allowed = char_budget.allow_call("recall_memory")
            emitted = char_budget.consume("R" * 300) if first_allowed else ""
            second_allowed = char_budget.allow_call("load_skill")
            char_state = char_budget.public_dict()
            chars_observed = {
                "initial_frame_chars": len(frame), "first_allowed": first_allowed,
                "emitted_chars": len(emitted), "second_allowed": second_allowed,
                "used_chars": char_state["used_chars"], "max_chars": char_state["max_chars"],
            }
            chars_expected = {
                "first_allowed": True, "emitted_chars": 100,
                "second_allowed": False, "used_equals_max": True,
            }
            _check(checks, "turn_memory_character_cap_includes_initial_frame", "working",
                   chars_observed, chars_expected,
                   first_allowed and len(emitted) == 100 and not second_allowed
                   and char_state["used_chars"] == char_limit)
        finally:
            for item in reversed(stores):
                item.close()

    passed = sum(item["pass"] for item in checks)
    return {
        "measurement_date": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
        "seed": seed,
        "scope": "offline local mechanism regression on synthetic fixtures; no model or network",
        "protocol": {
            "prediction_source": "actual local module calls against temporary SQLite and temporary SKILL.md files",
            "expected_use": "fixed fixture expectations are used only after observations are collected",
            "clock": "2026-01-01T00:00:00+00:00; advanced by two days for TTL checks",
            "limitations": [
                "not a public benchmark, model-answer-quality result, or online latency measurement",
                "prompt budgets count Python characters, not provider tokens",
                "personal episodes and semantic memory do not establish multi-Agent chat isolation",
            ],
        },
        "limits": {
            "episode_top_k": 3, "episode_ttl_days": 1,
            "capacity_max_active": 2, "semantic_section_chars": 3000,
            "active_skill_section_chars": 3000, "assembled_frame_chars": 700,
            "prompt_chars": 2000, "protocol_prompt_chars": 3000,
            "turn_memory_max_calls": 2, "turn_memory_max_chars": 2000,
            "turn_memory_character_case_extra_chars": 100,
        },
        "environment": {
            "python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
        },
        "summary": {"passed": passed, "total": len(checks),
                    "pass_rate": round(passed / len(checks), 6) if checks else 0.0},
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run offline five-layer memory mechanism regression")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--min-pass-rate", type=float, default=1.0)
    parser.add_argument("--out", type=Path, help="Write the complete JSON report")
    args = parser.parse_args(argv)
    if not 0 <= args.min_pass_rate <= 1:
        parser.error("--min-pass-rate must be between 0 and 1")
    try:
        result = run(args.seed)
        serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(serialized, encoding="utf-8")
        print(serialized, end="")
        return 0 if result["summary"]["pass_rate"] >= args.min_pass_rate else 1
    except Exception as exc:
        print(f"memory layer evaluation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
