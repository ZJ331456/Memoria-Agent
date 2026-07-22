from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class EvaluationReport:
    cases: int
    recall_at_k: float
    mean_reciprocal_rank: float
    wrong_injection_rate: float


def evaluate_rankings(cases: list[dict[str, Any]], predictions: dict[str, list[str]], k: int = 5) -> EvaluationReport:
    """Score retrieval output by expected IDs; empty expected sets are negative/gating cases."""
    recalls, reciprocal, wrong, negatives = [], [], 0, 0
    for case in cases:
        case_id = str(case["id"])
        expected = {str(item) for item in case.get("expected_ids", [])}
        ranked = [str(item) for item in predictions.get(case_id, [])[:k]]
        if not expected:
            negatives += 1
            wrong += int(bool(ranked))
            continue
        hits = expected.intersection(ranked)
        recalls.append(len(hits) / len(expected))
        ranks = [ranked.index(item) + 1 for item in expected if item in ranked]
        reciprocal.append(1 / min(ranks) if ranks else 0.0)
    return EvaluationReport(
        len(cases), round(sum(recalls) / max(1, len(recalls)), 4),
        round(sum(reciprocal) / max(1, len(reciprocal)), 4), round(wrong / max(1, negatives), 4),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Memoria retrieval rankings")
    parser.add_argument("predictions", type=Path, help="JSON object: case id -> ranked memory IDs")
    parser.add_argument("--cases", type=Path, default=Path(__file__).with_name("memory_cases.json"))
    parser.add_argument("-k", type=int, default=5)
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    predictions = json.loads(args.predictions.read_text(encoding="utf-8"))
    print(json.dumps(asdict(evaluate_rankings(cases, predictions, args.k)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
