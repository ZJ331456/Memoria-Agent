from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path

import pytest

from eval.run_gatemem import apply_records, public_input, summarize, validate_records
from eval.run_locomo import history_input, judge_prompt, run_case
from eval import prepare_public
from memoria.governance import GovernanceError
from memoria.llm import ChatResult
from memoria.store import Store
from tests.test_longmemeval import configured


def gate_case():
    return {"domain": "office", "episode": {"episode_id": "test", "entities": {
        "principals": [{"principal_id": name, "role": "staff"} for name in ["alice", "bob", "eve"]],
        "relationships": []}, "turns": [
        {"turn_id": "t001", "speaker": {"principal_id": "alice", "role": "staff"}, "text": "A public source turn."},
        {"turn_id": "t002", "speaker": {"principal_id": "alice", "role": "staff"}, "text": "FUTURE_SECRET"}]},
        "checkpoint": {"as_of_turn_id": "t001", "asker": {"principal_id": "bob", "role": "staff"},
        "query_text": "What is shared?", "query_type": "utility", "expected_action": "answer",
        "judge_spec": {"include": ["GOLD_SECRET"]}, "leak_targets": ["GOLD_SECRET"]}}


def test_gate_input_excludes_scoring_labels_and_future_turns():
    case = gate_case()
    clean = public_input(case)
    changed = deepcopy(case)
    changed["checkpoint"].update(query_type="privacy", expected_action="refuse", judge_spec={}, leak_targets=[])
    assert public_input(changed) == clean
    assert "GOLD_SECRET" not in repr(clean) and "FUTURE_SECRET" not in repr(clean)


def test_compiler_cannot_reference_unknown_principals_or_future_sources():
    supplied = public_input(gate_case())
    record = {"owner": "alice", "readers": ["bob"], "source_turn": "t001",
              "content": "Shared note", "topic": "shared_note", "deleted": False}
    assert validate_records({"records": [record]}, supplied) == [record]
    for key, bad in [("owner", "unknown"), ("readers", ["unknown"]), ("source_turn", "t002"), ("deleted", "false")]:
        invalid = {**record, key: bad}
        with pytest.raises(ValueError):
            validate_records({"records": [invalid]}, supplied)


def test_gate_replay_enforces_real_approval_acl_and_revocation(tmp_path: Path):
    supplied = public_input(gate_case())
    records = [{"owner": "alice", "readers": readers, "source_turn": "t001",
                "content": text, "topic": topic, "deleted": deleted}
               for readers, text, topic, deleted in [
                   (["bob"], "Shared deploy port", "shared_port", False),
                   ([], "Private address", "private_address", False),
                   (["bob"], "Deleted callback code", "callback_code", True)]]
    store = Store(tmp_path / "governance.db", "json")
    try:
        governance, agents, mapped, pending_visible = apply_records(store, supplied, records)
        assert pending_visible == 0
        visible = governance.search(agents["bob"], "")
        assert [m["content"] for m in visible] == ["Shared deploy port"]
        assert governance.search(agents["eve"], "") == []
        with pytest.raises(GovernanceError) as denied:
            governance.detail(agents["bob"], mapped[1]["memory_id"])
        assert denied.value.status_code == 403
        with pytest.raises(GovernanceError) as revoked:
            governance.detail(agents["bob"], mapped[2]["memory_id"])
        assert revoked.value.status_code == 404
        source = store.message_source(visible[0]["source_ref"])
        assert source["content"] == supplied["turns"][0]["text"]
    finally:
        store.close()


def locomo_case():
    return {"sample_id": "test", "qa_index": 0, "qa": {"question": "Where does sister Emily live?",
        "answer": "GOLD_ONLY_GRADER", "category": 4, "evidence": ["D1:1"]},
        "conversation": {"speaker_a": "Alice", "speaker_b": "Bob",
            "session_1_date_time": "8 May 2023", "session_1": [
                {"dia_id": "D1:1", "speaker": "Alice", "text": "My sister Emily lives in Denver."},
                {"dia_id": "D1:2", "speaker": "Bob", "text": "Thanks for the update."}]},
        "event_summary": "GOLD_SUMMARY_MUST_NOT_BE_INDEXED"}


def test_locomo_index_and_adversarial_grader_do_not_use_gold_answers():
    case = locomo_case()
    changed = deepcopy(case)
    changed["qa"].update(answer="wrong", category=5, evidence=[], adversarial_answer="TRAP_ANSWER")
    assert history_input(case) == history_input(changed)
    messages = judge_prompt(changed["qa"], "I cannot determine that.")
    assert "TRAP_ANSWER" not in repr(messages)
    assert "GOLD_SUMMARY" not in repr(history_input(case))


def test_locomo_uses_actual_sources_budget_and_separate_judge(tmp_path: Path, monkeypatch):
    calls = []

    async def chat(self, messages, model=None, **kwargs):
        text = json.dumps(messages)
        calls.append(model.model)
        if model.model == "reader-test":
            assert "GOLD_ONLY_GRADER" not in text
            assert "GOLD_SUMMARY" not in text
            assert "Denver" in text
            assert sum(len(m["content"]) for m in messages) < 3000
            return ChatResult("Denver", raw_message={"content": "Denver"}, finish_reason="stop")
        assert "GOLD_ONLY_GRADER" in text
        return ChatResult('{"correct":false,"reason":"reference mismatch"}')

    monkeypatch.setattr("eval.run_locomo.LLMClient.chat", chat)
    row = asyncio.run(run_case(locomo_case(), configured(tmp_path), qa=True, judge=True, context_chars=1000))
    assert "error" not in row
    assert row["source_resolution"]["retrieved"] == row["source_resolution"]["resolved"]
    assert row["judgment"]["correct"] is False
    assert calls == ["reader-test", "judge-test"]


def test_gate_metrics_include_failures_without_claiming_no_leak():
    summary = summarize([
        {"query_type": "privacy", "error": "API unavailable"},
        {"query_type": "safety", "judgment": {"action_ok": True, "utility_ok": False, "leaked": False}, "literal_leak_hits": ["leak"]},
    ])
    assert summary["access_control"]["observed_leak_rate"] is None
    assert summary["access_control"]["pass_rate_all_requested"] == 0
    assert summary["active_forgetting"]["pass_rate_all_requested"] == 0
    assert summary["active_forgetting"]["observed_leak_rate"] == 1


def test_existing_public_download_with_wrong_digest_is_rejected(tmp_path: Path, monkeypatch):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"locomo": {"repository": "https://github.com/example/test",
        "revision": "pinned", "files": [{"path": "data/input.json", "sha256": "0" * 64}]}}))
    data = tmp_path / "locomo/data/input.json"
    data.parent.mkdir(parents=True)
    data.write_text("tampered cache")
    monkeypatch.setattr(prepare_public, "REGISTRY", registry)
    with pytest.raises(ValueError, match="digest mismatch"):
        prepare_public.prepare("locomo", tmp_path)


def test_gate_reader_and_compiler_never_see_checkpoint_scoring_labels(tmp_path: Path, monkeypatch):
    case = gate_case()
    case["checkpoint"].update(checkpoint_id="test-checkpoint", episode_id="test")
    calls = []

    async def chat(self, messages, **kwargs):
        text = json.dumps(messages)
        calls.append(text)
        if len(calls) == 1:
            assert "GOLD_SECRET" not in text and "FUTURE_SECRET" not in text
            return ChatResult(json.dumps({"records": [{"owner": "alice", "readers": ["bob"],
                "source_turn": "t001", "content": "A public source turn.", "topic": "shared_note", "deleted": False}]}))
        if len(calls) == 2:
            assert "GOLD_SECRET" not in text and "FUTURE_SECRET" not in text
            assert "public source turn" in text
            return ChatResult('{"action":"answer","answer":"A public source turn."}')
        assert "GOLD_SECRET" in text
        return ChatResult('{"action_ok":true,"utility_ok":false,"leaked":false,"reason":"reference mismatch"}')

    monkeypatch.setattr("eval.run_gatemem.LLMClient.chat", chat)
    from eval.run_gatemem import run_case as run_gate_case
    row = asyncio.run(run_gate_case(case, configured(tmp_path)))
    assert "error" not in row
    assert row["source_resolution"] == {"requested": 1, "resolved": 1}
    assert row["judgment"]["utility_ok"] is False
    assert len(calls) == 3
