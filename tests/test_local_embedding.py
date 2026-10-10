from __future__ import annotations

import asyncio
import json
from pathlib import Path
from threading import BoundedSemaphore, Event

import httpx
import pytest

from memoria.config import ModelConfig, Settings
from memoria.memory.embedding import EmbeddingClient, EmbeddingError
from memoria.memory import local_bge
from memoria.memory.engine import MemoryEngine
from memoria.models_config import public_models, test_model_slot as check_model_slot
from memoria.store import Store


def _model_files(path: Path) -> Path:
    """A fingerprint fixture, never a loadable/downloaded model."""
    path.mkdir()
    (path / "model.safetensors").write_bytes(b"fake-weights-v1")
    (path / "config.json").write_text('{"model_type":"bert","hidden_size":512}', encoding="utf-8")
    (path / "tokenizer.json").write_text('{"revision":1}', encoding="utf-8")
    return path


def _client(path: Path, device: str = "cpu", api_key: str = "") -> EmbeddingClient:
    return EmbeddingClient(ModelConfig(str(path), api_key, f"local://{device}"))


def test_changed_weights_are_rejected_before_lazy_load(tmp_path):
    model = _model_files(tmp_path / "model")
    fingerprint = local_bge.model_fingerprint(model)
    encoder = local_bge.LocalBgeEncoder(model, "cpu", fingerprint)
    (model / "model.safetensors").write_bytes(b"replacement-weights")
    try:
        with pytest.raises(RuntimeError, match="模型文件已改变"):
            encoder._load()
    finally:
        encoder._executor.shutdown(wait=True)


def _settings(tmp_path: Path, model: Path) -> Settings:
    config = tmp_path / "local-test.toml"
    config.write_text(
        f'[storage]\ndatabase = "{(tmp_path / "state.db").as_posix()}"\n'
        '[llm.main]\nmodel = "remote-test"\napi_key = "test-key"\nbase_url = "https://example.test/v1"\n'
        f'[memory.embedding]\nmodel = "{model.as_posix()}"\nbase_url = "local://cpu"\n',
        encoding="utf-8",
    )
    return Settings.load(config)


class _FakeEncoder:
    def __init__(self):
        self.requests: list[list[str]] = []

    async def encode(self, texts: list[str], timeout: float):
        self.requests.append(list(texts))
        return [[1.0, *([0.0] * 511)] for _ in texts]


def test_local_model_is_configured_without_api_key(tmp_path):
    model = _model_files(tmp_path / "model")
    settings = _settings(tmp_path, model)
    assert settings.embedding.api_key == ""
    assert EmbeddingClient(settings.embedding).enabled
    assert settings.public_dict()["embedding"]["configured"]
    assert public_models(settings)["embedding"] == {
        "model": str(model).replace("\\", "/"), "base_url": "local://cpu",
        "configured": True, "api_key_set": False, "local": True,
    }
    assert not EmbeddingClient(ModelConfig("remote", "", "https://example.test/v1")).enabled
    # Process-local local:// is valid for LLM and embedding when model+device are set.
    settings.main = settings.fast = ModelConfig(str(model), "", "local://cpu")
    assert settings.public_dict()["main"]["configured"]
    assert settings.public_dict()["fast"]["configured"]
    assert public_models(settings)["main"]["configured"]
    assert public_models(settings)["fast"]["configured"]
    assert public_models(settings)["main"]["local"] is True


@pytest.mark.parametrize("config", [
    ModelConfig("remote-test", "test-key", "https://example.test/v1"),
    ModelConfig("", "", "local://cpu"),
])
def test_warmup_skips_remote_and_disabled_local(config, monkeypatch):
    async def reject_embedding(self, text):
        pytest.fail("warmup must not encode remote or disabled models")

    def reject_http(**kwargs):
        pytest.fail("warmup must not create an HTTP client")

    monkeypatch.setattr(EmbeddingClient, "embed", reject_embedding)
    monkeypatch.setattr("memoria.memory.embedding.httpx.AsyncClient", reject_http)
    asyncio.run(EmbeddingClient(config).warmup())


@pytest.mark.parametrize("request_timeout", [0.01, 30, 180])
def test_local_warmup_extends_cold_timeout_then_reuses_encoder(tmp_path, monkeypatch, request_timeout):
    model = _model_files(tmp_path / "model")
    instances = {}

    class TimingEncoder(_FakeEncoder):
        def __init__(self):
            super().__init__()
            self.timeouts = []

        async def encode(self, texts, timeout):
            self.timeouts.append(timeout)
            return await super().encode(texts, timeout)

    def get_encoder(path, device, fingerprint):
        identity = (path, device, fingerprint)
        if identity not in instances:
            instances[identity] = TimingEncoder()
        return instances[identity]

    monkeypatch.setattr(local_bge, "get_encoder", get_encoder)
    client = EmbeddingClient(ModelConfig(str(model), "", "local://cpu"), timeout_seconds=request_timeout)
    original_namespace = client.namespace

    async def scenario():
        await client.warmup()
        assert client.timeout_seconds == request_timeout
        assert len(await client.embed_query("用户喜欢什么饮料")) == 512

    asyncio.run(scenario())
    assert len(instances) == 1
    assert next(iter(instances)) == (str(model.resolve()), "cpu", original_namespace)
    encoder = next(iter(instances.values()))
    assert len(encoder.requests) == 2
    assert encoder.timeouts[0] >= max(120, request_timeout)
    assert encoder.timeouts[1] == request_timeout
    assert not encoder.requests[0][0].startswith(local_bge.QUERY_INSTRUCTION)
    assert encoder.requests[1] == [local_bge.QUERY_INSTRUCTION + "用户喜欢什么饮料"]
    assert client.namespace == original_namespace


def test_local_warmup_reports_failure_without_mutating_timeout(tmp_path, monkeypatch):
    model = _model_files(tmp_path / "model")

    class FailingEncoder:
        async def encode(self, texts, timeout):
            raise RuntimeError("simulated local model load failure")

    monkeypatch.setattr(local_bge, "get_encoder", lambda *args: FailingEncoder())
    client = _client(model)
    with pytest.raises(EmbeddingError, match="simulated local model load failure"):
        asyncio.run(client.warmup())
    assert client.timeout_seconds == 30


def test_instruction_is_added_only_to_local_query(tmp_path, monkeypatch):
    model = _model_files(tmp_path / "model")
    encoder = _FakeEncoder()
    identities = []

    def get_encoder(path, device, fingerprint):
        identities.append((path, device, fingerprint))
        return encoder

    monkeypatch.setattr(local_bge, "get_encoder", get_encoder)
    client = _client(model)

    async def scenario():
        assert len(await client.embed_query("我喜欢什么饮料")) == 512
        await client.embed("用户通常喝茶")
        await client.embed_batch(["用户住在北京", "用户研究长期记忆"])

    asyncio.run(scenario())
    assert encoder.requests == [
        [local_bge.QUERY_INSTRUCTION + "我喜欢什么饮料"],
        ["用户通常喝茶"], ["用户住在北京", "用户研究长期记忆"],
    ]
    assert all(path == str(model.resolve()) and device == "cpu" and fingerprint == client.namespace
               for path, device, fingerprint in identities)


def test_remote_query_keeps_existing_http_payload(monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]})

    real_client = httpx.AsyncClient
    monkeypatch.setattr("memoria.memory.embedding.httpx.AsyncClient",
                        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs))
    client = EmbeddingClient(ModelConfig("remote-test", "test-key", "https://example.test/v1"))
    assert asyncio.run(client.embed_query("用户偏好是什么")) == [1.0, 0.0]
    assert str(requests[0].url) == "https://example.test/v1/embeddings"
    assert requests[0].headers["Authorization"] == "Bearer test-key"
    assert json.loads(requests[0].content) == {
        "model": "remote-test", "input": ["用户偏好是什么"], "encoding_format": "float",
    }


def test_engine_backfill_and_writes_use_documents_but_retrieval_uses_query(tmp_path, monkeypatch):
    model = _model_files(tmp_path / "model")
    encoder = _FakeEncoder()
    monkeypatch.setattr(local_bge, "get_encoder", lambda *args: encoder)
    store = Store(tmp_path / "memory.db", vector_backend="json")
    engine = MemoryEngine(store, _client(model))
    previous = store.add_memory("用户通常喝茶", "preference")

    async def scenario():
        recalled = await engine.retrieve("饮料偏好", expand_graph=False)
        assert recalled[0]["id"] == previous["id"]
        saved = await engine.remember("用户住在北京", "profile", 3, "test")
        assert saved.action == "created"
        corrected = await engine.correct(saved.memory["id"], "用户住在上海", "profile", 3, "用户搬家")
        assert corrected["content"] == "用户住在上海"

    try:
        asyncio.run(scenario())
        assert encoder.requests == [
            ["用户通常喝茶"], [local_bge.QUERY_INSTRUCTION + "饮料偏好"],
            ["用户住在北京"], ["用户住在上海"],
        ]
    finally:
        store.close()


@pytest.mark.parametrize("filename,new_content", [
    ("model.safetensors", b"fake-weights-v2"),
    ("config.json", b'{"model_type":"bert","hidden_size":512,"revision":2}'),
    ("tokenizer.json", b'{"revision":2}'),
])
def test_namespace_changes_with_weights_or_encoder_files(tmp_path, filename, new_content):
    model = _model_files(tmp_path / "model")
    original_client = _client(model)
    before = original_client.namespace
    (model / filename).write_bytes(new_content)
    assert _client(model).namespace != before
    # An in-flight client retains its captured identity; switching creates a new client.
    assert original_client.namespace == before


def test_namespace_ignores_device_key_and_download_metadata(tmp_path):
    model = _model_files(tmp_path / "model")
    before = _client(model, "cpu").namespace
    assert _client(model, "cuda", "unused-local-key").namespace == before
    assert _client(model, "auto").namespace == before
    (model / "download_manifest.json").write_text('{"downloaded_at":"later"}', encoding="utf-8")
    assert _client(model).namespace == before


def test_namespace_changes_with_encoding_policy(tmp_path, monkeypatch):
    model = _model_files(tmp_path / "model")
    before = _client(model).namespace
    monkeypatch.setattr(local_bge, "POLICY", local_bge.POLICY + ":changed-instruction")
    assert _client(model).namespace != before


def test_local_model_test_encodes_locally_without_http(tmp_path, monkeypatch):
    model = _model_files(tmp_path / "model")
    settings = _settings(tmp_path, model)
    encoder = _FakeEncoder()
    monkeypatch.setattr(local_bge, "get_encoder", lambda *args: encoder)

    def reject_http(**kwargs):
        raise AssertionError("local model tests must not create an HTTP client")

    monkeypatch.setattr("memoria.models_config.httpx.AsyncClient", reject_http)
    result = asyncio.run(check_model_slot(settings, "embedding"))
    assert result["ok"] and result["dimension"] == 512
    assert result["namespace"] == EmbeddingClient(settings.embedding).namespace
    assert encoder.requests == [[local_bge.QUERY_INSTRUCTION + "用户喜欢喝茶"]]


def test_local_model_test_reports_encoder_failure(tmp_path, monkeypatch):
    model = _model_files(tmp_path / "model")
    settings = _settings(tmp_path, model)

    class FailingEncoder:
        async def encode(self, texts, timeout):
            raise RuntimeError("CUDA unavailable in test")

    monkeypatch.setattr(local_bge, "get_encoder", lambda *args: FailingEncoder())
    result = asyncio.run(check_model_slot(settings, "embedding"))
    assert not result["ok"]
    assert "CUDA unavailable in test" in result["message"]


def test_invalid_local_device_does_not_fall_back_to_network(tmp_path, monkeypatch):
    model = _model_files(tmp_path / "model")
    monkeypatch.setattr(local_bge, "get_encoder", lambda *args: pytest.fail("invalid device must fail first"))
    with pytest.raises(EmbeddingError, match="local://cuda"):
        asyncio.run(_client(model, "unknown").embed("文本"))


def test_timeout_keeps_running_work_in_bounded_queue(tmp_path, monkeypatch):
    encoder = local_bge.LocalBgeEncoder(tmp_path, "cpu")
    encoder._pending = BoundedSemaphore(1)
    started, release = Event(), Event()
    completed = []

    def blocking_encode(texts):
        started.set()
        assert release.wait(3)
        completed.append(list(texts))
        return [[1.0, 0.0]]

    monkeypatch.setattr(encoder, "_encode", blocking_encode)

    async def scenario():
        first = asyncio.create_task(encoder.encode(["running"], timeout=0.1))
        assert await asyncio.to_thread(started.wait, 1)
        with pytest.raises(asyncio.TimeoutError):
            await first
        with pytest.raises(RuntimeError, match="队列已满"):
            await encoder.encode(["must-not-start"], timeout=1)
        release.set()
        await asyncio.to_thread(encoder._executor.shutdown, True)

    try:
        asyncio.run(scenario())
        assert completed == [["running"]]
        assert encoder._pending.acquire(blocking=False)
        encoder._pending.release()
    finally:
        release.set()
        encoder._executor.shutdown(wait=True)


def test_cancelled_queued_work_is_skipped_and_releases_capacity(tmp_path, monkeypatch):
    encoder = local_bge.LocalBgeEncoder(tmp_path, "cpu")
    encoder._pending = BoundedSemaphore(2)
    started, release = Event(), Event()
    completed = []

    def blocking_encode(texts):
        if texts == ["running"]:
            started.set()
            assert release.wait(3)
        completed.append(list(texts))
        return [[1.0, 0.0]]

    monkeypatch.setattr(encoder, "_encode", blocking_encode)

    async def scenario():
        first = asyncio.create_task(encoder.encode(["running"], timeout=2))
        assert await asyncio.to_thread(started.wait, 1)
        cancelled = asyncio.create_task(encoder.encode(["cancelled"], timeout=2))
        await asyncio.sleep(0)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        # asyncio.wrap_future propagates cancellation to the queued concurrent future.
        await asyncio.sleep(0)
        replacement = asyncio.create_task(encoder.encode(["replacement"], timeout=2))
        await asyncio.sleep(0)
        release.set()
        assert await first == [[1.0, 0.0]]
        assert await replacement == [[1.0, 0.0]]

    try:
        asyncio.run(scenario())
        assert completed == [["running"], ["replacement"]]
    finally:
        release.set()
        encoder._executor.shutdown(wait=True)


def test_executor_submit_failure_releases_reserved_capacity(tmp_path):
    encoder = local_bge.LocalBgeEncoder(tmp_path, "cpu")
    encoder._pending = BoundedSemaphore(1)
    encoder._executor.shutdown(wait=True)
    with pytest.raises(RuntimeError):
        asyncio.run(encoder.encode(["cannot-submit"], timeout=1))
    assert encoder._pending.acquire(blocking=False)
    encoder._pending.release()
