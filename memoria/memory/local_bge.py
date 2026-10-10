"""Offline BGE inference; torch/transformers are optional and loaded lazily."""
from __future__ import annotations

import asyncio
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from threading import BoundedSemaphore
from typing import Any

QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："
MAX_LENGTH = 512
POLICY = "bge-zh-v1.5:cls:l2:fp32:max512:query-instruction-v1"
_ROOT = Path(__file__).resolve().parents[2]


def model_path(model: str) -> Path:
    path = Path(model).expanduser()
    return (path if path.is_absolute() else _ROOT / path).resolve()


def model_fingerprint(path: Path) -> str:
    """Hash actual model/tokenizer files once per client, not the device or API key."""
    digest = hashlib.sha256(POLICY.encode())
    files = sorted({*path.glob("*.safetensors"), *path.glob("*.json"), *path.glob("*.txt")})
    files = [file for file in files if file.name != "download_manifest.json"]
    if not files:
        digest.update(str(path).encode())
    for file in files:
        digest.update(file.name.encode())
        with file.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


class LocalBgeEncoder:
    """One bounded, serial GPU worker per model identity, shared by local clients."""

    def __init__(self, path: Path, device: str, fingerprint: str | None = None):
        self.path = path
        self.device = device
        self.fingerprint = fingerprint
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="memoria-bge")
        self._pending = BoundedSemaphore(8)
        self._model: Any = None
        self._tokenizer: Any = None
        self._torch: Any = None
        self._actual_device = ""

    def _load(self) -> None:
        if self._model is not None:
            return
        if not self.path.is_dir() or not list(self.path.glob("*.safetensors")):
            raise RuntimeError(f"本地 BGE 模型缺失：{self.path}；请先运行 scripts/download_embedding.py")
        if self.fingerprint and model_fingerprint(self.path) != self.fingerprint:
            raise RuntimeError("本地模型文件已改变，请重新创建 Embedding 客户端后再编码")
        config = json.loads((self.path / "config.json").read_text(encoding="utf-8"))
        if config.get("model_type") != "bert" or config.get("hidden_size") != 512:
            raise RuntimeError("本地后端仅支持 512 维 BGE 中文 small v1.5 模型")
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError("本地 Embedding 需要 torch 和 transformers，请使用项目 .venv") from exc
        device = self.device
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA 不可用，请使用已安装 CUDA PyTorch 的 .venv，或选择 local://cpu")
        tokenizer = AutoTokenizer.from_pretrained(self.path, local_files_only=True, trust_remote_code=False)
        model = AutoModel.from_pretrained(
            self.path, local_files_only=True, trust_remote_code=False, use_safetensors=True,
        ).to(device=device, dtype=torch.float32).eval()
        self._tokenizer, self._model, self._torch = tokenizer, model, torch
        self._actual_device = device

    def _encode(self, texts: list[str]) -> list[list[float]]:
        self._load()
        encoded = self._tokenizer(texts, padding=True, truncation=True, max_length=MAX_LENGTH, return_tensors="pt")
        encoded = {key: value.to(self._actual_device) for key, value in encoded.items()}
        with self._torch.inference_mode():
            hidden = self._model(**encoded).last_hidden_state[:, 0]
            vectors = self._torch.nn.functional.normalize(hidden.float(), p=2, dim=1)
            return vectors.cpu().tolist()

    async def encode(self, texts: list[str], timeout: float) -> list[list[float]]:
        if not self._pending.acquire(blocking=False):
            raise RuntimeError("本地 Embedding 队列已满，请稍后重试")
        # Canceling a queued future removes its work; active CUDA work completes once.
        # Keep its queue slot until the worker actually finishes, including on timeout.
        try:
            future = self._executor.submit(self._encode, texts)
        except BaseException:
            self._pending.release()
            raise
        future.add_done_callback(lambda _: self._pending.release())
        return await asyncio.wait_for(asyncio.wrap_future(future), timeout=timeout)

    def public_status(self) -> dict[str, Any]:
        return {"device": self._actual_device or self.device, "dimension": 512,
                "max_length": MAX_LENGTH, "loaded": self._model is not None}


@lru_cache(maxsize=2)
def get_encoder(path: str, device: str, fingerprint: str) -> LocalBgeEncoder:
    # fingerprint prevents reuse after in-place model/tokenizer changes.
    return LocalBgeEncoder(Path(path), device, fingerprint.removeprefix("embedding-local:"))
