"""Deterministic scoring for the governance/shared-memory mini benchmark.

This module scores externally produced predictions. It does not simulate a
memory engine or turn fixture answers into predictions.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


DEFAULT_CASES = Path(__file__).with_name("governance_cases.json")


@dataclass(slots=True)
class CaseScore:
    id: str
    passed: bool
    missing_prediction: bool
    checks: dict[str, bool]


@dataclass(slots=True)
class GovernanceReport:
    cases: int
    evaluated_cases: int
    case_pass_rate: float
    authorized_recall_at_k: float
    authorized_precision_at_k: float
    isolation_leak_rate: float
    lifecycle_accuracy: float
    decision_accuracy: float
    conflict_review_accuracy: float
    source_exact_match_rate: float
    missing_prediction_ids: list[str]
    details: list[CaseScore]


def _fraction(numerator: float, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _strings(value: Any, *, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a list of strings")
    return value


def _source_set(value: Any, *, label: str) -> set[tuple[str, str, str]]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    sources: set[tuple[str, str, str]] = set()
    for item in value:
        if not isinstance(item, dict) or any(not isinstance(item.get(key), str) for key in ("memory_id", "message_id", "session_id")):
            raise ValueError(f"{label} items need string memory_id, message_id and session_id")
        sources.add((item["memory_id"], item["message_id"], item["session_id"]))
    return sources


def _prediction(case_id: str, raw: dict[str, Any]) -> tuple[list[str], set[tuple[str, str, str]], str | None]:
    ranked = _strings(raw.get("memory_ids", []), label=f"{case_id}.memory_ids")
    sources = _source_set(raw.get("sources", []), label=f"{case_id}.sources")
    decision = raw.get("decision")
    if decision is not None and not isinstance(decision, str):
        raise ValueError(f"{case_id}.decision must be a string")
    return ranked, sources, decision


def evaluate_governance(
    fixture: dict[str, Any], predictions: dict[str, dict[str, Any]], k: int = 5
) -> GovernanceReport:
    """Score predictions keyed by case ID; absent cases fail every applicable check.

    Forbidden IDs and sources are checked in the full returned output, not just
    top K. Only authorized recall/precision use the top K ranking.
    """
    if k < 1:
        raise ValueError("k must be at least 1")
    if not isinstance(predictions, dict):
        raise ValueError("predictions must be an object keyed by case ID")
    cases = fixture["cases"]
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("case IDs must be unique")
    unknown = set(predictions) - set(ids)
    if unknown:
        raise ValueError(f"unknown case IDs: {', '.join(sorted(unknown))}")

    details: list[CaseScore] = []
    recalls: list[float] = []
    precisions: list[float] = []
    isolation_violations: list[bool] = []
    lifecycle_results: list[bool] = []
    decision_results: list[bool] = []
    conflict_results: list[bool] = []
    source_results: list[bool] = []
    missing_ids: list[str] = []

    for case in cases:
        case_id = case["id"]
        missing = case_id not in predictions
        if missing:
            missing_ids.append(case_id)
        raw = predictions.get(case_id, {})
        if not isinstance(raw, dict):
            raise ValueError(f"{case_id} prediction must be an object")
        ranked, sources, decision = _prediction(case_id, raw)
        source_messages = {source[1] for source in sources}
        checks = set(case.get("checks", []))
        expected = set(case.get("expected_ids", []))
        forbidden = set(case.get("forbidden_ids", []))
        forbidden_source_ids = set(case.get("forbidden_source_ids", []))
        top_k = ranked[:k]
        no_forbidden = not (forbidden.intersection(ranked) or forbidden_source_ids.intersection(source_messages))
        if case["operation"] in {"retrieve", "inspect"}:
            # An empty expected set is a negative case: any returned memory fails.
            memory_ok = bool(not missing and "memory_ids" in raw and no_forbidden)
            memory_ok &= expected.issubset(top_k) if expected else not ranked
        else:
            memory_ok = True
        checks_result: dict[str, bool] = {}
        if case["operation"] in {"retrieve", "inspect"}:
            checks_result["memory"] = memory_ok

        if "authorized_recall" in checks:
            if not expected:
                raise ValueError(f"{case_id} authorized_recall needs expected_ids")
            hits = len(expected.intersection(top_k)) if not missing and "memory_ids" in raw else 0
            recalls.append(hits / len(expected))
            precisions.append(hits / len(top_k) if top_k else 0.0)
            checks_result["authorized_recall"] = bool(not missing and memory_ok)

        if "isolation" in checks:
            leaked = bool(forbidden.intersection(ranked) or forbidden_source_ids.intersection(source_messages))
            # Unauthorized writes are an isolation violation when accepted.
            if case["operation"] == "propose":
                leaked |= decision != "reject"
            isolation_violations.append(missing or leaked)
            checks_result["isolation"] = bool(not missing and not leaked)

        if "lifecycle" in checks:
            lifecycle_results.append(memory_ok)
            checks_result["lifecycle"] = memory_ok

        if "decision" in checks:
            decision_ok = bool(not missing and "decision" in raw and decision == case["expected_decision"])
            decision_results.append(decision_ok)
            checks_result["decision"] = decision_ok
            if "conflict" in checks:
                conflict_results.append(decision_ok)
                checks_result["conflict"] = decision_ok

        if "provenance" in checks:
            expected_sources = _source_set(case["expected_sources"], label=f"{case_id}.expected_sources")
            source_ok = bool(not missing and "sources" in raw and sources == expected_sources and no_forbidden)
            source_results.append(source_ok)
            checks_result["provenance"] = source_ok

        # A source leak fails its case even if no provenance check was requested.
        if case["operation"] in {"retrieve", "inspect"} and not no_forbidden:
            checks_result["memory"] = False
        details.append(CaseScore(case_id, all(checks_result.values()) and not missing, missing, checks_result))

    return GovernanceReport(
        cases=len(cases),
        evaluated_cases=len(cases) - len(missing_ids),
        case_pass_rate=_fraction(sum(item.passed for item in details), len(cases)),
        authorized_recall_at_k=_fraction(sum(recalls), len(recalls)),
        authorized_precision_at_k=_fraction(sum(precisions), len(precisions)),
        isolation_leak_rate=_fraction(sum(isolation_violations), len(isolation_violations)),
        lifecycle_accuracy=_fraction(sum(lifecycle_results), len(lifecycle_results)),
        decision_accuracy=_fraction(sum(decision_results), len(decision_results)),
        conflict_review_accuracy=_fraction(sum(conflict_results), len(conflict_results)),
        source_exact_match_rate=_fraction(sum(source_results), len(source_results)),
        missing_prediction_ids=missing_ids,
        details=details,
    )


def prediction_template(fixture: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Produce an empty answer sheet without copying expected answers."""
    template: dict[str, dict[str, Any]] = {}
    for case in fixture["cases"]:
        entry: dict[str, Any] = {}
        if case["operation"] in {"retrieve", "inspect"}:
            entry["memory_ids"] = []
        if "decision" in case.get("checks", []):
            entry["decision"] = ""
        if "provenance" in case.get("checks", []):
            entry["sources"] = []
        template[case["id"]] = entry
    return template


def main() -> None:
    parser = argparse.ArgumentParser(description="Score governance and shared-memory predictions")
    parser.add_argument("predictions", type=Path, nargs="?", help="JSON object: case ID -> memory_ids / decision / sources")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--write-template", type=Path, help="Write an empty prediction template and exit")
    parser.add_argument("-k", type=int, default=5)
    parser.add_argument("--min-pass-rate", type=float, help="Exit with status 1 below this case pass rate")
    parser.add_argument("--max-leak-rate", type=float, help="Exit with status 1 above this isolation leak rate")
    args = parser.parse_args()
    fixture = json.loads(args.cases.read_text(encoding="utf-8"))
    if args.write_template:
        args.write_template.write_text(json.dumps(prediction_template(fixture), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return
    if args.predictions is None:
        parser.error("predictions is required unless --write-template is used")
    predictions = json.loads(args.predictions.read_text(encoding="utf-8"))
    report = evaluate_governance(fixture, predictions, args.k)
    print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    if args.min_pass_rate is not None and report.case_pass_rate < args.min_pass_rate:
        raise SystemExit(1)
    if args.max_leak_rate is not None and report.isolation_leak_rate > args.max_leak_rate:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
