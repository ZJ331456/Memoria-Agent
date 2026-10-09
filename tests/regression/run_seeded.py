from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from dataclasses import asdict
from pathlib import Path

from memoria.config import Settings
from memoria.memory import EmbeddingClient, MemoryEngine, MemoryQueryPlanner
from memoria.store import Store

from .memory_eval import evaluate_rankings


async def run(use_embedding: bool, k: int) -> dict:
    source = Path(__file__).with_name("seeded_memory_cases.json")
    fixture = json.loads(source.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="memoria-eval-") as directory:
        store = Store(Path(directory) / "eval.db", "json")
        labels: dict[str, str] = {}
        reverse: dict[str, str] = {}
        for item in fixture["memories"]:
            saved = store.add_memory(item["content"], item["kind"], item["importance"], "eval")
            labels[item["id"]] = saved["id"]
            reverse[saved["id"]] = item["id"]
        embedder = None
        if use_embedding:
            settings = Settings.load()
            embedder = EmbeddingClient(settings.embedding, min(settings.request_timeout_seconds, 30), settings.max_retries)
        engine = MemoryEngine(store, embedder)
        planner = MemoryQueryPlanner()
        predictions = {}
        for case in fixture["cases"]:
            plan = await planner.plan(case["query"], [])
            results = await engine.retrieve(plan.query, k) if plan.needed else []
            predictions[case["id"]] = [reverse[item["id"]] for item in results if item["id"] in reverse]
        report = evaluate_rankings(fixture["cases"], predictions, k)
        return {"report": asdict(report), "predictions": predictions}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the seeded Memoria retrieval benchmark")
    parser.add_argument("--embedding", action="store_true", help="Use the configured live embedding model")
    parser.add_argument("-k", type=int, default=5)
    parser.add_argument("--min-recall", type=float, default=0.0)
    args = parser.parse_args()
    result = asyncio.run(run(args.embedding, args.k))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["report"]["recall_at_k"] < args.min_recall:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
