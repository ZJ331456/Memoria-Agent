from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from tests.regression.governance_eval import evaluate_governance, prediction_template
from tests.regression.run_governance import run


FIXTURE = json.loads(Path(__file__).with_name("governance_cases.json").read_text(encoding="utf-8"))


def complete_predictions() -> dict[str, dict]:
    """Build a scoring oracle for testing the evaluator, not a system runner."""
    predictions = {}
    for case in FIXTURE["cases"]:
        answer = {}
        if case["operation"] in {"retrieve", "inspect"}:
            answer["memory_ids"] = list(case["expected_ids"])
        if "provenance" in case["checks"]:
            answer["sources"] = deepcopy(case["expected_sources"])
        if "decision" in case["checks"]:
            answer["decision"] = case["expected_decision"]
        predictions[case["id"]] = answer
    return predictions


def test_complete_predictions_pass_every_dimension() -> None:
    report = evaluate_governance(FIXTURE, complete_predictions())
    assert report.cases == 24
    assert report.case_pass_rate == 1.0
    assert report.authorized_recall_at_k == 1.0
    assert report.isolation_leak_rate == 0.0
    assert report.lifecycle_accuracy == 1.0
    assert report.conflict_review_accuracy == 1.0
    assert report.source_exact_match_rate == 1.0


def test_hidden_source_leak_and_wrong_session_are_penalized() -> None:
    predictions = complete_predictions()
    predictions["private-peer-isolation"]["sources"] = [
        {"memory_id": "m-private-review", "message_id": "msg-private-1", "session_id": "session-alice-private"}
    ]
    predictions["team-peer-recall"]["sources"][0]["session_id"] = "wrong-session"
    report = evaluate_governance(FIXTURE, predictions)
    assert report.isolation_leak_rate == 0.1111
    assert report.source_exact_match_rate == 0.8
    assert {item.id for item in report.details if not item.passed} == {
        "private-peer-isolation", "team-peer-recall"
    }


def test_missing_predictions_fail_even_for_negative_cases() -> None:
    predictions = complete_predictions()
    del predictions["private-peer-isolation"]
    del predictions["expiry-after"]
    report = evaluate_governance(FIXTURE, predictions)
    assert report.evaluated_cases == 22
    assert report.missing_prediction_ids == ["private-peer-isolation", "expiry-after"]
    assert report.isolation_leak_rate == 0.1111
    assert report.lifecycle_accuracy == 0.8333
    assert report.case_pass_rate == 0.9167


def test_prediction_template_has_no_expected_answers() -> None:
    template = prediction_template(FIXTURE)
    assert template["team-peer-recall"] == {"memory_ids": [], "sources": []}
    assert template["contradictory-team-write"] == {"decision": ""}
    assert evaluate_governance(FIXTURE, template).case_pass_rate < 1


def test_malformed_prediction_is_rejected() -> None:
    predictions = complete_predictions()
    predictions["team-peer-recall"]["memory_ids"] = "m-alpha-release"
    with pytest.raises(ValueError, match="memory_ids"):
        evaluate_governance(FIXTURE, predictions)


def test_real_governance_runner_passes_synthetic_regression() -> None:
    result = run(FIXTURE)
    assert len(result["predictions"]) == 24
    assert result["report"]["case_pass_rate"] == 1.0
    assert result["report"]["isolation_leak_rate"] == 0.0
    assert result["report"]["source_exact_match_rate"] == 1.0
