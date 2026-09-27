from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from ..skills import SkillCatalog
from ..store import Store
from ..tools.policy import ToolAuthorization

logger = logging.getLogger(__name__)

DRIFT_SESSION_TITLE = "〔Drift〕空闲整理"


class DriftWorker:
    """空闲时按预算执行限定技能的后台整理任务。"""

    def __init__(
        self,
        *,
        store: Store,
        runtime: Any,
        skills: SkillCatalog | None,
        markdown: Any | None,
        settings: Any,
        is_busy: Callable[[], bool] | None = None,
    ):
        self.store = store
        self.runtime = runtime
        self.skills = skills
        self.markdown = markdown
        self.settings = settings
        self.is_busy = is_busy or (lambda: False)
        self._running = False
        self._lock = asyncio.Lock()
        self.timezone = ZoneInfo(str(getattr(settings, "drift_timezone", "Asia/Shanghai")))
        self._skill_cursor = 0

    def status(self) -> dict[str, Any]:
        enabled = bool(self.settings.drift_enabled)
        self.store.expire_stale_drift_runs(older_than_seconds=max(900, self.settings.drift_interval_seconds))
        last = self.store.latest_drift_run()
        today = self.store.drift_runs_today(self.timezone)
        return {
            "enabled": enabled,
            "running": self._running,
            "busy": self.is_busy(),
            "min_idle_seconds": self.settings.drift_min_idle_seconds,
            "interval_seconds": self.settings.drift_interval_seconds,
            "max_steps": self.settings.drift_max_steps,
            "daily_budget": self.settings.drift_daily_budget,
            "runs_today": today,
            "budget_remaining": max(0, self.settings.drift_daily_budget - today) if enabled else 0,
            "quiet_hours": list(self.settings.drift_quiet_hours),
            "allowed_skills": list(self.settings.drift_allowed_skills),
            "allow_write_tools": list(self.settings.drift_allow_write_tools),
            "timezone": str(self.timezone),
            "last_run": last,
            "idle_seconds": self.store.seconds_since_last_user_message(
                exclude_session_titles=(DRIFT_SESSION_TITLE,),
            ),
            "session_id": self._find_session_id(),
        }

    def recent_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.store.list_drift_runs(limit)

    async def run_loop(self) -> None:
        poll = max(15.0, min(60.0, float(self.settings.drift_interval_seconds) / 10))
        while True:
            try:
                if self.settings.drift_enabled:
                    await self.maybe_run(trigger="scheduler")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Drift 调度失败")
            await asyncio.sleep(poll)

    async def maybe_run(self, *, trigger: str = "scheduler", force: bool = False) -> dict[str, Any] | None:
        async with self._lock:
            return await self._run_locked(trigger=trigger, force=force)

    async def _run_locked(self, *, trigger: str, force: bool) -> dict[str, Any] | None:
        if not self.settings.drift_enabled and not force:
            return {"skipped": True, "reason": "disabled"}
        if self._running:
            return {"skipped": True, "reason": "already_running"}
        # Never overlap a live user turn, even on forced runs.
        if self.is_busy():
            return {"skipped": True, "reason": "user_turn_active"}
        self.store.expire_stale_drift_runs(older_than_seconds=max(900, self.settings.drift_interval_seconds))
        now_local = datetime.now(self.timezone)
        if not force and now_local.hour in set(self.settings.drift_quiet_hours):
            return {"skipped": True, "reason": f"quiet_hour:{now_local.hour}"}
        idle = self.store.seconds_since_last_user_message(exclude_session_titles=(DRIFT_SESSION_TITLE,))
        if not force and idle is not None and idle < self.settings.drift_min_idle_seconds:
            return {"skipped": True, "reason": "not_idle", "idle_seconds": idle}
        last = self.store.latest_drift_run()
        if not force and last and last.get("status") in {"completed", "failed"}:
            try:
                last_at = datetime.fromisoformat(str(last["created_at"]))
                if last_at.tzinfo is None:
                    last_at = last_at.replace(tzinfo=timezone.utc)
                elapsed = (datetime.now(self.timezone) - last_at.astimezone(self.timezone)).total_seconds()
                if elapsed < self.settings.drift_interval_seconds:
                    return {"skipped": True, "reason": "cooldown", "elapsed_seconds": int(elapsed)}
            except ValueError:
                pass
        if last and last.get("status") == "running":
            return {"skipped": True, "reason": "previous_still_running"}
        if not force and self.store.drift_runs_today(self.timezone) >= self.settings.drift_daily_budget:
            return {"skipped": True, "reason": "daily_budget"}
        skill_name = self._pick_skill()
        if not skill_name:
            return {"skipped": True, "reason": "no_skill"}
        return await self._execute(skill_name=skill_name, trigger=trigger)

    def _pick_skill(self) -> str | None:
        allowed = list(self.settings.drift_allowed_skills)
        if not allowed:
            return None
        if not self.skills:
            return allowed[0]
        available = [name for name in allowed if self.skills.get(name)]
        if not available:
            return allowed[0]
        name = available[self._skill_cursor % len(available)]
        self._skill_cursor += 1
        return name

    def _find_session_id(self) -> str | None:
        for session in self.store.sessions():
            if session.get("title") == DRIFT_SESSION_TITLE:
                return session["id"]
        return None

    def _ensure_session(self) -> str:
        existing = self._find_session_id()
        if existing:
            return existing
        return self.store.create_session(DRIFT_SESSION_TITLE)["id"]

    async def _execute(self, *, skill_name: str, trigger: str) -> dict[str, Any]:
        session_id = self._ensure_session()
        run_id = uuid.uuid4().hex
        self._running = True
        self.store.create_drift_run(
            run_id=run_id,
            session_id=session_id,
            skill=skill_name,
            trigger=trigger,
            status="running",
        )
        skill_body = ""
        if self.skills:
            record = self.skills.get(skill_name)
            if record:
                skill_body = record.body
        prompt = self._build_prompt(skill_name, skill_body)
        # Hard-block destructive tools even if misconfigured in allow_write_tools.
        blocked = {"forget_memory"}
        allowed = {name for name in self.settings.drift_allow_write_tools if name not in blocked}
        authorization = ToolAuthorization(
            allowed,
            reason=f"drift allowlist:{','.join(sorted(allowed)) or 'none'}",
        )
        try:
            message, _created, trace = await self.runtime.run(
                session_id,
                prompt,
                authorization=authorization,
                iteration_limit=self.settings.drift_max_steps,
                skip_memory_enqueue=True,
                force_skill=skill_name,
                turn_kind="drift",
            )
            summary = (message.get("content") or "")[:2000]
            if self.markdown and self.markdown.enabled and summary.strip():
                self.markdown.append_pending(
                    source_ref=f"drift:{run_id}",
                    content=f"[Drift/{skill_name}] {summary[:400]}",
                    kind="procedure",
                )
            result = self.store.finish_drift_run(
                run_id,
                status="completed",
                summary=summary,
                steps=int(trace.get("steps") or 0),
                trace_id=str(trace.get("id") or ""),
                error="",
            )
            await self.runtime.event_bus.emit(
                "drift.completed",
                {"run_id": run_id, "skill": skill_name, "session_id": session_id},
            )
            return result
        except asyncio.CancelledError:
            self.store.finish_drift_run(run_id, status="cancelled", summary="", steps=0, trace_id="", error="cancelled")
            raise
        except Exception as exc:
            logger.exception("Drift 执行失败 run=%s", run_id)
            return self.store.finish_drift_run(
                run_id,
                status="failed",
                summary="",
                steps=0,
                trace_id="",
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            self._running = False

    @staticmethod
    def _build_prompt(skill_name: str, skill_body: str) -> str:
        body = skill_body.strip() or f"执行空闲技能 {skill_name}。"
        return (
            f"[Drift 空闲任务 / skill={skill_name}]\n"
            "你正在无人值守的后台整理轮次。遵守技能说明书；优先只读观察与小结。\n"
            "不要调用 forget_memory；除非授权清单明确允许，否则不要 memorize。\n"
            "完成后用简洁中文给出：做了什么、发现了什么、下一步建议。\n\n"
            f"## Skill: {skill_name}\n{body}"
        )
