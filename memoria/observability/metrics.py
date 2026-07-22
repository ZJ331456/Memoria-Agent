from __future__ import annotations

import threading
from collections import defaultdict

from ..store import Store


def _label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class MetricRegistry:
    """Small dependency-free Prometheus exporter for a single Memoria process."""

    def __init__(self):
        self.lock = threading.Lock()
        self.requests: dict[tuple[str, str, int], int] = defaultdict(int)
        self.duration: dict[tuple[str, str], tuple[int, float]] = {}

    def observe_http(self, method: str, route: str, status: int, duration_seconds: float) -> None:
        with self.lock:
            self.requests[(method, route, status)] += 1
            count, total = self.duration.get((method, route), (0, 0.0))
            self.duration[(method, route)] = (count + 1, total + duration_seconds)

    def render(self, store: Store) -> str:
        lines = [
            "# HELP memoria_http_requests_total HTTP requests handled.",
            "# TYPE memoria_http_requests_total counter",
        ]
        with self.lock:
            for (method, route, status), value in sorted(self.requests.items()):
                lines.append(f'memoria_http_requests_total{{method="{_label(method)}",route="{_label(route)}",status="{status}"}} {value}')
            lines.extend(["# HELP memoria_http_request_duration_seconds HTTP request duration.", "# TYPE memoria_http_request_duration_seconds summary"])
            for (method, route), (count, total) in sorted(self.duration.items()):
                labels = f'method="{_label(method)}",route="{_label(route)}"'
                lines.append(f"memoria_http_request_duration_seconds_count{{{labels}}} {count}")
                lines.append(f"memoria_http_request_duration_seconds_sum{{{labels}}} {total:.6f}")
        summary = store.observability_summary()
        lines.extend([
            "# HELP memoria_turns_total Agent turns by status.",
            "# TYPE memoria_turns_total gauge",
        ])
        for status, value in sorted(summary["turns"].items()):
            lines.append(f'memoria_turns_total{{status="{_label(status)}"}} {value}')
        lines.extend([
            f'memoria_turn_duration_milliseconds_avg {summary["turn_duration_ms_avg"]:.3f}',
            "# TYPE memoria_llm_requests_total counter",
            f'memoria_llm_requests_total {summary["runtime"].get("llm_requests", 0)}',
            "# TYPE memoria_llm_retries_total counter",
            f'memoria_llm_retries_total {summary["runtime"].get("llm_retries", 0)}',
            "# TYPE memoria_llm_tokens_total counter",
            f'memoria_llm_tokens_total {summary["runtime"].get("llm_tokens", 0)}',
            "# TYPE memoria_llm_duration_milliseconds_total counter",
            f'memoria_llm_duration_milliseconds_total {summary["runtime"].get("llm_duration_ms", 0)}',
            "# HELP memoria_memory_jobs Memory jobs by status.",
            "# TYPE memoria_memory_jobs gauge",
        ])
        for status, value in sorted(summary["jobs"].items()):
            lines.append(f'memoria_memory_jobs{{status="{_label(status)}"}} {value}')
        lines.append(f'memoria_active_memories {summary["active_memories"]}')
        return "\n".join(lines) + "\n"
