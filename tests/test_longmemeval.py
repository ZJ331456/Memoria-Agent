from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from eval.longmemeval import aggregate, evidence_metrics, load_cases, parse_judgment, system_input
from eval.run_longmemeval import Options, TrackedEmbedder, document_vectors, final_answer, run
from memoria.config import Settings
from memoria.llm import ChatResult, LLMClient


def fixture_case(qid="example"):
    return {
        "question_id": qid, "question_type": "single-session-user",
        "question": "Where does my sister Emily live?", "question_date": "2023/06/01 12:00",
        "answer": "Denver", "answer_session_ids": ["answer_gold"],
        "haystack_session_ids": ["filler", "answer_gold"],
        "haystack_dates": ["2023/05/01 12:00", "2023/05/02 12:00"],
        "haystack_sessions": [
            [{"role": "user", "content": "Help with a python script."}],
            [{"role": "user", "content": "My sister Emily lives in Denver.", "has_answer": True},
             {"role": "assistant", "content": "Thanks for telling me."}],
        ],
    }


def configured(tmp_path: Path):
    config = tmp_path / "config.toml"
    config.write_text(f'''[storage]
database="{tmp_path / 'isolated.db'}"
[llm.main]
model="reader-test"
api_key="test-key"
base_url="https://example.test/v1"
[llm.fast]
model="judge-test"
api_key="test-key"
base_url="https://example.test/v1"
[memory.embedding]
model="embedding-test"
api_key="test-key"
base_url="https://example.test/v1"
''')
    return Settings.load(config)


def test_system_input_cannot_use_gold_labels_or_answer_prefixed_ids():
    case = fixture_case()
    clean = system_input(case)
    changed = fixture_case()
    changed["answer"] = "UNIQUE_GOLD_SECRET"
    changed["answer_session_ids"] = ["filler"]
    changed["haystack_session_ids"] = ["answer_fake", "unrelated"]
    changed["question_type"] = "temporal-reasoning"
    for session in changed["haystack_sessions"]:
        for turn in session:
            turn["has_answer"] = not turn.get("has_answer", False)
    assert clean == system_input(changed)
    assert "answer_gold" not in repr(clean) and "has_answer" not in repr(clean)


def test_loader_rejects_bad_alignment_but_accepts_duplicate_source_ids(tmp_path: Path):
    case = fixture_case()
    case["haystack_session_ids"][0] = "answer_gold"
    path = tmp_path / "cases.json"
    path.write_text(json.dumps([case]))
    assert load_cases(path)[0]["question_id"] == "example"
    case["haystack_dates"].pop()
    path.write_text(json.dumps([case]))
    with pytest.raises(ValueError, match="equal"):
        load_cases(path)


def test_evidence_metrics_score_missing_sources_and_exclude_abstention():
    case = fixture_case()
    docs = list(system_input(case).documents)
    assert evidence_metrics(case, docs[:1])["session"]["recall"] == 0
    metrics = evidence_metrics(case, docs[1:])
    assert metrics["session"]["all_found"] and metrics["turn"]["recall"] == 1
    case["question_id"] += "_abs"
    assert evidence_metrics(case, docs)["session"]["eligible"] is False


def test_real_retriever_isolates_cases_and_exports_null_qa_without_api(tmp_path: Path):
    first = fixture_case("alice")
    second = fixture_case("bob")
    second["haystack_sessions"][1][0]["content"] = "The library is in Paris."
    path = tmp_path / "cases.json"
    path.write_text(json.dumps([first, second]))
    report = asyncio.run(run(path, configured(tmp_path), Options()))
    a, b = report["results"]
    assert a["retrieved"]["session"]["recall"] == 1
    assert b["retrieved"]["session"]["recall"] == 0
    assert report["metrics"]["overall"]["qa_accuracy"] is None
    assert not report["models"]


def test_reader_never_sees_reference_but_judge_does(tmp_path: Path, monkeypatch):
    case = fixture_case()
    case["answer"] = "ONLY_THE_GRADER_MAY_SEE_THIS"
    path = tmp_path / "cases.json"
    path.write_text(json.dumps([case]))
    calls = []

    async def chat(self, messages, model=None, **kwargs):
        text = json.dumps(messages)
        calls.append((model.model, text))
        if model.model == "reader-test":
            assert case["answer"] not in text
            assert "answer_gold" not in text and "has_answer" not in text
            return ChatResult("Denver", usage={"total_tokens": 12})
        assert case["answer"] in text
        return ChatResult('{"correct":false,"reason":"reference mismatch"}')

    monkeypatch.setattr("eval.run_longmemeval.LLMClient.chat", chat)
    report = asyncio.run(run(path, configured(tmp_path), Options(qa=True, judge=True)))
    assert len(calls) == 2
    assert report["results"][0]["hypothesis"] == "Denver"
    assert report["metrics"]["overall"]["qa_accuracy"] == 0


def test_embedding_failure_is_recorded_not_a_fake_hybrid_success(tmp_path: Path, monkeypatch):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps([fixture_case()]))

    async def broken(self, texts):
        raise RuntimeError("offline test provider")

    monkeypatch.setattr(TrackedEmbedder, "_request", broken)
    report = asyncio.run(run(path, configured(tmp_path), Options(embedding=True)))
    assert report["metrics"]["overall"]["errors"] == 1
    assert "retrieved" not in report["results"][0]
    assert report["protocol"]["retriever"] == "hybrid"
    metrics = report["metrics"]["overall"]["retrieved_session"]
    assert metrics["eligible_cases"] == 0 and metrics["requested_eligible_cases"] == 1
    assert metrics["evidence_recall_all_requested"] == 0


def test_embedding_cache_uses_model_content_not_credentials(tmp_path: Path, monkeypatch):
    calls = []

    async def embed(self, texts):
        calls.append(texts)
        return [[1.0, 2.0] for _ in texts]

    monkeypatch.setattr(TrackedEmbedder, "embed_batch", embed)
    client = TrackedEmbedder(configured(tmp_path))
    vectors, cached = asyncio.run(document_vectors(client, ["content"], tmp_path / "cache"))
    assert not cached
    client.config.api_key = "different-key"
    again, cached = asyncio.run(document_vectors(client, ["content"], tmp_path / "cache"))
    assert cached and again == vectors and len(calls) == 1
    client.config.model = "different-model"
    asyncio.run(document_vectors(client, ["content"], tmp_path / "cache"))
    assert len(calls) == 2


def test_grading_rejects_nonboolean_and_failed_cases_remain_in_denominator():
    with pytest.raises(ValueError):
        parse_judgment('{"correct":"true"}')
    report = aggregate([
        {"question_type": "single-session-user", "judgment": {"correct": True}},
        {"question_type": "single-session-user", "error": "provider failed"},
    ])["overall"]
    assert report["qa_accuracy"] == 1
    assert report["qa_accuracy_all_requested"] == .5


def test_reasoning_only_and_truncated_completions_cannot_be_scored():
    with pytest.raises(ValueError, match="final content"):
        final_answer(ChatResult("hidden reasoning", raw_message={"content": None,
            "reasoning_content": "hidden reasoning"}, finish_reason="stop"))
    with pytest.raises(ValueError, match="length"):
        final_answer(ChatResult("an incomplete answer", finish_reason="length"))
    assert final_answer(ChatResult("Denver", raw_message={"content": "Denver"}, finish_reason="stop")) == "Denver"


def test_provider_finish_reason_is_preserved_for_evaluation(tmp_path: Path):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
        "choices": [{"finish_reason": "length", "message": {
            "content": None, "reasoning_content": "unfinished internal analysis"}}],
        "usage": {"completion_tokens": 12},
    }))
    result = asyncio.run(LLMClient(configured(tmp_path), transport).chat([
        {"role": "user", "content": "Where does Emily live?"}]))
    assert result.finish_reason == "length"
    with pytest.raises(ValueError, match="length"):
        final_answer(result)
