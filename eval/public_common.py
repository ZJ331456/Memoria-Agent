"""Small shared helpers for public-data adapters; no local benchmark fixtures."""
from __future__ import annotations

import json
import platform
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

from .longmemeval import file_digest
from .run_longmemeval import final_answer


def json_result(result) -> dict:
    content = final_answer(result)
    match = re.search(r"\{[\s\S]*\}", content)
    data = json.loads(match.group() if match else content)
    if not isinstance(data, dict):
        raise ValueError("model must return a JSON object")
    return data


def call_stats(result):
    return {"usage": result.usage, "duration_ms": result.duration_ms,
            "retries": result.retries, "finish_reason": result.finish_reason}


def metadata(path: Path, payload: dict, protocol: dict, settings, slots: tuple[str, ...]):
    return {"measurement_date": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
            "dataset": {"file": path.name, "sha256": file_digest(path),
                        "source": payload["source"], "cases": min(5, len(payload["cases"]))},
            "protocol": protocol,
            "models": {slot: {"model": getattr(settings, slot).model,
                              "base_url": getattr(settings, slot).base_url} for slot in slots},
            "environment": {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version}}


def latency(samples: list[float]):
    return {"count": len(samples), "median_ms": round(median(samples), 3) if samples else None,
            "min_ms": round(min(samples), 3) if samples else None,
            "max_ms": round(max(samples), 3) if samples else None,
            "scope": "five public checkpoints at most; local calls only, not an SLO or load test"}


def write_report(path: Path, report: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


def configured(settings, slots):
    for slot in slots:
        model = getattr(settings, slot)
        if not (model.model and model.api_key and model.base_url):
            raise ValueError(f"{slot} model configuration is incomplete")


def api_totals(results, phases):
    return {phase: {"calls": sum(phase + "_call" in row for row in results),
                   "usage": {key: sum(row.get(phase + "_call", {}).get("usage", {}).get(key, 0)
                                      for row in results)
                             for key in ("prompt_tokens", "completion_tokens", "total_tokens")}}
            for phase in phases}
