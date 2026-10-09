"""Local, model-free latency benchmark for governed shared memory."""

from __future__ import annotations

import argparse
import json
import math
import random
import tempfile
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from memoria.governance import GovernanceError, MemoryGovernance
from memoria.store import Store


def _ms(nanoseconds: int) -> float:
    return round(nanoseconds / 1_000_000, 3)


def _latencies(samples: list[int]) -> dict[str, float]:
    ordered = sorted(samples)
    return {
        "p50": _ms(ordered[math.ceil(0.50 * len(ordered)) - 1]),
        "p95": _ms(ordered[math.ceil(0.95 * len(ordered)) - 1]),
        "max": _ms(ordered[-1]),
    }


def _denied(call: Any) -> bool:
    try:
        call()
    except GovernanceError as exc:
        return exc.status_code in {403, 404}
    return False


def run(memories: int = 1000, spaces: int = 10, queries: int = 200, seed: int = 331456) -> dict[str, Any]:
    """Seed through propose/approve, then time real authorized and denied calls."""
    if not 2 <= spaces <= 50:
        raise ValueError("spaces must be between 2 and 50")
    if not spaces <= memories <= 5000:
        raise ValueError("memories must be between spaces and 5000")
    if not 1 <= queries <= 5000:
        raise ValueError("queries must be between 1 and 5000")

    rng = random.Random(seed)
    with tempfile.TemporaryDirectory(prefix="memoria-shared-benchmark-") as directory:
        store = Store(Path(directory) / "benchmark.db", "json")
        try:
            governance = MemoryGovernance(store)
            seed_started = perf_counter_ns()
            owner = governance.create_agent("benchmark-owner")["id"]
            groups: list[dict[str, str]] = []
            for index in range(spaces):
                space_id = governance.create_space(owner, f"benchmark-space-{index:03d}")["id"]
                reader = governance.create_agent(f"benchmark-reader-{index:03d}")["id"]
                contributor = governance.create_agent(f"benchmark-contributor-{index:03d}")["id"]
                governance.grant(owner, space_id, reader, "reader")
                governance.grant(owner, space_id, contributor, "contributor")
                groups.append({"space_id": space_id, "reader": reader, "contributor": contributor})

            records: list[dict[str, str]] = []
            for index in range(memories):
                group = groups[index % spaces]
                term = f"bench-key-{index:06d}"
                proposal = governance.propose(
                    group["contributor"], group["space_id"],
                    f"{term} synthetic shared note {rng.randrange(1_000_000):06d}",
                    topic_key=term, source_type="manual",
                )
                memory = governance.approve(owner, proposal["id"], reason="benchmark seed")
                records.append({**group, "memory_id": memory["id"], "term": term})
            seed_ms = _ms(perf_counter_ns() - seed_started)

            search_samples: list[int] = []
            detail_samples: list[int] = []
            denied_search = denied_detail = 0
            measured_started = perf_counter_ns()
            for _ in range(queries):
                record = records[rng.randrange(memories)]
                started = perf_counter_ns()
                found = governance.search(record["reader"], record["term"], record["space_id"], limit=5)
                search_samples.append(perf_counter_ns() - started)
                if not any(item["id"] == record["memory_id"] for item in found):
                    raise RuntimeError("authorized search failed to return the seeded memory")

                started = perf_counter_ns()
                detail = governance.detail(record["reader"], record["memory_id"])
                detail_samples.append(perf_counter_ns() - started)
                if detail["id"] != record["memory_id"]:
                    raise RuntimeError("authorized detail returned the wrong memory")

                outsider = groups[(int(record["term"][-6:]) % spaces + 1) % spaces]["reader"]
                denied_search += _denied(
                    lambda: governance.search(outsider, record["term"], record["space_id"], limit=5)
                )
                denied_detail += _denied(lambda: governance.detail(outsider, record["memory_id"]))
            query_phase_ms = _ms(perf_counter_ns() - measured_started)
        finally:
            store.close()

    return {
        "configuration": {"memories": memories, "spaces": spaces, "queries": queries, "seed": seed},
        "seed_ms": seed_ms,
        "query_phase_ms": query_phase_ms,
        "authorized_search_ms": _latencies(search_samples),
        "authorized_detail_ms": _latencies(detail_samples),
        "unauthorized_explicit_scope": {
            "search_denied": denied_search,
            "search_attempts": queries,
            "search_denied_rate": round(denied_search / queries, 4),
            "detail_denied": denied_detail,
            "detail_attempts": queries,
            "detail_denied_rate": round(denied_detail / queries, 4),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark local governed shared memory (no model or network)")
    parser.add_argument("--memories", type=int, default=1000, help="seed memories, spaces..5000")
    parser.add_argument("--spaces", type=int, default=10, help="shared spaces, 2..50")
    parser.add_argument("--queries", type=int, default=200, help="measured queries, 1..5000")
    parser.add_argument("--seed", type=int, default=331456)
    parser.add_argument("--output", type=Path, help="optional JSON output file")
    args = parser.parse_args()
    try:
        report = run(args.memories, args.spaces, args.queries, args.seed)
    except ValueError as exc:
        parser.error(str(exc))
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")


if __name__ == "__main__":
    main()
