"""In-process local chat LLM (transformers); optional, loaded lazily like local BGE."""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from threading import BoundedSemaphore
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
_TOOL_CALL_RE = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL | re.IGNORECASE)
_FUNCTION_RE = re.compile(r"<function=([^>\n]+)>\s*(.*?)</function>", re.DOTALL | re.IGNORECASE)
_PARAM_RE = re.compile(r"<parameter=([^>\n]+)>\s*(.*?)</parameter>", re.DOTALL | re.IGNORECASE)
_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)


def model_path(model: str) -> Path:
    path = Path(model).expanduser()
    return (path if path.is_absolute() else _ROOT / path).resolve()


def parse_local_completion(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Parse Qwen-style tool calls; strip thinking blocks from visible content."""
    calls: list[dict[str, Any]] = []
    for block in _TOOL_CALL_RE.findall(text or ""):
        for name, body in _FUNCTION_RE.findall(block):
            arguments: dict[str, Any] = {}
            for key, raw in _PARAM_RE.findall(body):
                value = raw.strip()
                try:
                    arguments[key.strip()] = json.loads(value)
                except json.JSONDecodeError:
                    arguments[key.strip()] = value
            calls.append({
                "id": f"local_{uuid.uuid4().hex[:12]}",
                "name": name.strip(),
                "arguments": arguments,
            })
    content = _TOOL_CALL_RE.sub("", text or "")
    content = _THINK_RE.sub("", content).strip()
    return content, calls


def _arguments_as_mapping(value: Any) -> dict[str, Any]:
    """Qwen chat_template does `arguments|items`; OpenAI wires often keep JSON strings."""
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"_raw": value}
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    return {"value": value}


def _normalize_tool_calls(tool_calls: Any) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    if not isinstance(tool_calls, list):
        return normalized
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        clone = dict(call)
        function = clone.get("function")
        if isinstance(function, dict):
            function = dict(function)
            function["arguments"] = _arguments_as_mapping(function.get("arguments"))
            if not function.get("name") and clone.get("name"):
                function["name"] = clone["name"]
            clone["function"] = function
        elif clone.get("name"):
            clone["function"] = {
                "name": clone["name"],
                "arguments": _arguments_as_mapping(clone.get("arguments")),
            }
        normalized.append(clone)
    return normalized


def normalize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep chat-template-friendly OpenAI-shaped messages."""
    normalized: list[dict[str, Any]] = []
    for item in messages:
        role = str(item.get("role") or "user")
        entry: dict[str, Any] = {"role": role}
        content = item.get("content")
        if content is not None:
            entry["content"] = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        elif role != "assistant":
            entry["content"] = ""
        if role == "assistant" and item.get("tool_calls"):
            entry["tool_calls"] = _normalize_tool_calls(item.get("tool_calls"))
        if role == "tool":
            if item.get("tool_call_id"):
                entry["tool_call_id"] = item["tool_call_id"]
            if item.get("name"):
                entry["name"] = item["name"]
        normalized.append(entry)
    return normalized


class LocalCausalLM:
    """One serial GPU/CPU worker per model+device; shared across LLMClient calls."""

    def __init__(self, path: Path, device: str):
        self.path = path
        self.device = device
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="memoria-llm")
        self._pending = BoundedSemaphore(4)
        self._model: Any = None
        self._tokenizer: Any = None
        self._torch: Any = None
        self._actual_device = ""

    def _load(self) -> None:
        if self._model is not None:
            return
        if not self.path.is_dir():
            raise RuntimeError(f"本地 LLM 模型目录不存在：{self.path}")
        weights = list(self.path.glob("*.safetensors")) + list(self.path.glob("*.bin"))
        if not weights and not (self.path / "model.safetensors.index.json").exists():
            raise RuntimeError(f"本地 LLM 权重缺失：{self.path}")
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError("本地 LLM 需要 torch 和 transformers，请使用项目 .venv") from exc
        device = self.device
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA 不可用，请使用已安装 CUDA PyTorch 的 .venv，或选择 local://cpu")
        dtype = torch.bfloat16 if device == "cuda" and torch.cuda.is_bf16_supported() else (
            torch.float16 if device == "cuda" else torch.float32
        )
        tokenizer = AutoTokenizer.from_pretrained(
            str(self.path), local_files_only=True, trust_remote_code=True,
        )
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token
        model = AutoModelForCausalLM.from_pretrained(
            str(self.path),
            local_files_only=True,
            trust_remote_code=True,
            dtype=dtype,
            low_cpu_mem_usage=True,
        )
        model.to(device)
        model.eval()
        self._tokenizer, self._model, self._torch = tokenizer, model, torch
        self._actual_device = device

    def _generate(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None, max_tokens: int) -> dict[str, Any]:
        self._load()
        conversation = normalize_messages(messages)
        kwargs: dict[str, Any] = {
            "tokenize": False,
            "add_generation_prompt": True,
            "enable_thinking": False,
        }
        if tools:
            kwargs["tools"] = tools
        try:
            prompt = self._tokenizer.apply_chat_template(conversation, **kwargs)
        except TypeError:
            kwargs.pop("enable_thinking", None)
            try:
                prompt = self._tokenizer.apply_chat_template(conversation, **kwargs)
            except TypeError:
                kwargs.pop("tools", None)
                prompt = self._tokenizer.apply_chat_template(conversation, **kwargs)
        encoded = self._tokenizer(prompt, return_tensors="pt")
        encoded = {key: value.to(self._actual_device) for key, value in encoded.items()}
        prompt_len = int(encoded["input_ids"].shape[-1])
        pad_id = self._tokenizer.pad_token_id
        eos_id = self._tokenizer.eos_token_id
        with self._torch.inference_mode():
            output = self._model.generate(
                **encoded,
                max_new_tokens=max(1, max_tokens),
                do_sample=False,
                pad_token_id=pad_id,
                eos_token_id=eos_id,
            )
        generated = output[0][prompt_len:]
        text = self._tokenizer.decode(generated, skip_special_tokens=True)
        content, calls = parse_local_completion(text)
        raw_calls = [
            {
                "id": call["id"],
                "type": "function",
                "function": {
                    "name": call["name"],
                    "arguments": json.dumps(call["arguments"], ensure_ascii=False),
                },
            }
            for call in calls
        ]
        return {
            "content": content,
            "tool_calls": calls,
            "raw_message": {
                "role": "assistant",
                "content": content or None,
                "tool_calls": raw_calls,
            },
            "usage": {
                "prompt_tokens": prompt_len,
                "completion_tokens": int(generated.numel()),
                "total_tokens": prompt_len + int(generated.numel()),
            },
            "finish_reason": "tool_calls" if calls else "stop",
            "device": self._actual_device,
        }

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
        timeout: float,
    ) -> dict[str, Any]:
        if not self._pending.acquire(blocking=False):
            raise RuntimeError("本地 LLM 队列已满，请稍后重试")
        try:
            future = self._executor.submit(self._generate, messages, tools, max_tokens)
        except BaseException:
            self._pending.release()
            raise
        future.add_done_callback(lambda _: self._pending.release())
        return await asyncio.wait_for(asyncio.wrap_future(future), timeout=timeout)

    def public_status(self) -> dict[str, Any]:
        return {
            "device": self._actual_device or self.device,
            "path": str(self.path),
            "loaded": self._model is not None,
        }


@lru_cache(maxsize=2)
def get_local_llm(path: str, device: str) -> LocalCausalLM:
    return LocalCausalLM(Path(path), device)
