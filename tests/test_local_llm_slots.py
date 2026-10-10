"""Remote API + keyless local OpenAI-compatible LLM slot wiring."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from memoria.config import ModelConfig, Settings, is_loopback_base_url
from memoria.llm import LLMClient
from memoria.models_config import public_models
from memoria.models_config import test_model_slot as probe_model_slot


def _settings(tmp_path: Path, main: ModelConfig, fast: ModelConfig | None = None) -> Settings:
    tmp_path.mkdir(parents=True, exist_ok=True)
    config = tmp_path / "config.toml"
    fast = fast or ModelConfig("", "", "")
    config.write_text(
        f'''[llm.main]
model = "{main.model}"
api_key = "{main.api_key}"
base_url = "{main.base_url}"
[llm.fast]
model = "{fast.model}"
api_key = "{fast.api_key}"
base_url = "{fast.base_url}"
[storage]
database = "{(tmp_path / "local-llm.db").as_posix()}"
''',
        encoding="utf-8",
    )
    return Settings.load(config)


def test_loopback_detection():
    assert is_loopback_base_url("http://127.0.0.1:8080/v1")
    assert is_loopback_base_url("http://localhost:11434/v1")
    assert is_loopback_base_url("http://[::1]:8080/v1")
    assert not is_loopback_base_url("https://api.deepseek.com/v1")
    assert not is_loopback_base_url("local://cuda")
    assert not is_loopback_base_url("")


def test_process_local_llm_is_ready_without_api_key():
    config = ModelConfig("model/Qwen3.5-2B", "", "local://cuda")
    assert config.is_local_process()
    assert config.is_ready(role="llm")
    assert not config.is_local_http()


def test_parse_qwen_tool_calls():
    from memoria.local_llm import parse_local_completion

    text = """好的，我来计算。
<tool_call>
<function=calculate>
<parameter=expression>
1+2
</parameter>
</function>
</tool_call>
"""
    content, calls = parse_local_completion(text)
    assert "tool_call" not in content
    assert calls[0]["name"] == "calculate"
    assert calls[0]["arguments"]["expression"] == "1+2"


def test_normalize_messages_parses_openai_tool_argument_strings():
    from memoria.local_llm import normalize_messages

    messages = normalize_messages(
        [
            {"role": "user", "content": "1+1"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "calculate", "arguments": '{"expression":"1+1"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "2"},
        ]
    )
    args = messages[1]["tool_calls"][0]["function"]["arguments"]
    assert isinstance(args, dict)
    assert args["expression"] == "1+1"


def test_keyless_loopback_main_is_ready_remote_is_not(tmp_path: Path):
    local = _settings(tmp_path, ModelConfig("Qwen3.5-0.8B", "", "http://127.0.0.1:8080/v1"))
    assert local.main.is_ready(role="llm")
    assert local.main.is_local_http()
    assert local.public_dict()["setup_needed"] is False
    assert public_models(local)["main"] == {
        "model": "Qwen3.5-0.8B",
        "base_url": "http://127.0.0.1:8080/v1",
        "configured": True,
        "api_key_set": False,
        "local": True,
        "backend": "openai-local",
    }
    assert local.main.auth_headers()["Authorization"] == "Bearer local"

    remote = _settings(tmp_path / "remote", ModelConfig("deepseek-v4-flash", "", "https://api.deepseek.com/v1"))
    assert not remote.main.is_ready(role="llm")
    assert remote.public_dict()["setup_needed"] is True
    assert public_models(remote)["main"]["configured"] is False


def test_remote_with_key_still_ready(tmp_path: Path):
    settings = _settings(tmp_path, ModelConfig("deepseek-v4-flash", "sk-test", "https://api.deepseek.com/v1"))
    assert settings.main.is_ready(role="llm")
    assert not settings.main.is_local_http()
    assert settings.public_dict()["setup_needed"] is False
    assert settings.main.auth_headers()["Authorization"] == "Bearer sk-test"


def test_preferred_chat_model_uses_keyless_local_fast(tmp_path: Path):
    settings = _settings(
        tmp_path,
        ModelConfig("main-remote", "sk-main", "https://api.example.test/v1"),
        ModelConfig("local-fast", "", "http://localhost:8080/v1"),
    )
    selected = settings.preferred_chat_model(prefer_fast=True)
    assert selected.model == "local-fast"
    assert selected.is_local_http()


def test_llm_client_accepts_keyless_loopback(tmp_path: Path):
    settings = _settings(tmp_path, ModelConfig("local-main", "", "http://127.0.0.1:9/v1"))
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization", "")
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "pong"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    result = asyncio.run(
        LLMClient(settings, transport=httpx.MockTransport(handler)).chat(
            [{"role": "user", "content": "ping"}],
            max_tokens=8,
        )
    )
    assert result.content == "pong"
    assert seen["auth"] == "Bearer local"
    assert seen["url"].endswith("/chat/completions")


def test_test_model_slot_allows_keyless_loopback(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path, ModelConfig("local-main", "", "http://127.0.0.1:8080/v1"))

    class FakeResponse:
        status_code = 200
        text = "ok"

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers=None, json=None):
            assert headers["Authorization"] == "Bearer local"
            assert url.endswith("/chat/completions")
            return FakeResponse()

    monkeypatch.setattr("memoria.models_config.httpx.AsyncClient", FakeClient)
    result = asyncio.run(probe_model_slot(settings, "main"))
    assert result["ok"] is True
    assert "本地" in result["message"]
