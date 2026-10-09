import json

from tests.regression import run_memory_layers
from memoria.memory.episodic import EpisodicMemory


def test_offline_layer_regression_reports_real_module_observations():
    report = run_memory_layers.run(seed=331456)
    assert report["summary"] == {"passed": 15, "total": 15, "pass_rate": 1.0}
    assert report["protocol"]["prediction_source"].startswith("actual local module calls")
    assert all({"observed", "expected", "pass", "layer"} <= item.keys()
               for item in report["checks"])
    assert {
        "episode_cross_session_recall", "episode_unrelated_abstention",
        "episode_source_resolution", "episode_ttl_filters_search",
        "episode_pin_survives_ttl", "forgetting_dry_run_has_no_effect",
        "episode_capacity_archiving", "source_session_deletion_no_episode_leak",
        "semantic_pending_review_not_injected", "skill_revision_reload_visible",
        "long_semantic_does_not_evict_active_skill",
        "prompt_cap_preserves_frame_and_latest_user", "prompt_tool_protocol_complete",
        "turn_memory_call_cap_includes_initial_frame",
        "turn_memory_character_cap_includes_initial_frame",
    } == {item["name"] for item in report["checks"]}


def test_search_regression_is_reported_as_failure_and_cli_returns_nonzero(monkeypatch, tmp_path):
    real_search = EpisodicMemory.search

    def broken_search(self, query, *args, **kwargs):
        if query == "部署脚本路径":
            return []
        return real_search(self, query, *args, **kwargs)

    monkeypatch.setattr(EpisodicMemory, "search", broken_search)
    output = tmp_path / "regression.json"
    assert run_memory_layers.main(["--out", str(output), "--min-pass-rate", "1.0"]) == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    recall = next(item for item in report["checks"]
                  if item["name"] == "episode_cross_session_recall")
    assert recall["observed"] == []
    assert recall["expected"] == ["deployment"]
    assert recall["pass"] is False
    assert report["summary"]["passed"] < report["summary"]["total"]
