"""Offline encoding and memory retrieval smoke check; never calls a LLM API."""
from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import math
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from memoria.config import ModelConfig
from memoria.memory.embedding import EmbeddingClient
from memoria.memory.engine import MemoryEngine
from memoria.memory.local_bge import MAX_LENGTH, POLICY, QUERY_INSTRUCTION, get_encoder
from memoria.store import Store


def check_api(model_dir: Path, device: str) -> dict:
    """Exercise the real setup/model-test route against an isolated database."""
    from fastapi.testclient import TestClient
    from memoria.api import create_app

    with tempfile.TemporaryDirectory(prefix="memoria-bge-api-") as temporary:
        folder = Path(temporary)
        config = folder / "smoke.toml"
        config.write_text(
            f'[storage]\ndatabase="{(folder / "api.db").as_posix()}"\n'
            '[agent.skills]\nenabled=false\n[agent.mcp]\nenabled=false\n'
            '[memory.markdown]\nenabled=false\n'
            f'[memory.embedding]\nmodel="{model_dir.as_posix()}"\nbase_url="local://{device}"\n',
            encoding="utf-8",
        )
        app = create_app(config)
        started = time.perf_counter()
        with TestClient(app) as api:
            startup_ms = (time.perf_counter() - started) * 1000
            setup = api.get("/api/setup/status")
            assert setup.status_code == 200 and setup.json()["embedding"]["configured"]
            assert not setup.json()["embedding"]["api_key_set"]
            response = api.post("/api/settings/models/test", json={"slot": "embedding"})
            result = response.json()
            assert response.status_code == 200 and result["ok"] and result["dimension"] == 512
            return {**result, "startup_including_warmup_ms": round(startup_ms, 3)}


async def check(args: argparse.Namespace) -> dict:
    started = time.perf_counter()
    import torch

    model_dir = args.model_dir.expanduser().resolve()
    client = EmbeddingClient(ModelConfig(str(model_dir), "", f"local://{args.device}"), timeout_seconds=120)
    passages = [
        "用户平时喜欢喝绿茶，不喝咖啡。",
        "用户养了一只叫团子的猫。",
        "用户正在研究人工智能的长期记忆管理。",
        "用户的生日是五月十二日。",
    ]
    vectors = await client.embed_batch(passages)
    cold_ms = (time.perf_counter() - started) * 1000
    norms = [math.sqrt(sum(x * x for x in vector)) for vector in vectors]
    assert len(vectors) == len(passages) and all(len(vector) == 512 for vector in vectors)
    assert all(math.isfinite(x) for vector in vectors for x in vector)
    assert all(abs(norm - 1) < 1e-5 for norm in norms)
    query = "我平时更喜欢什么饮料？"
    query_vector = await client.embed_query(query)
    similarities = [sum(x * y for x, y in zip(query_vector, vector)) for vector in vectors]
    assert max(range(len(passages)), key=similarities.__getitem__) == 0

    elapsed = []
    for _ in range(args.rounds):
        started = time.perf_counter()
        await client.embed_query(query)
        elapsed.append((time.perf_counter() - started) * 1000)

    retrieval = []
    with tempfile.TemporaryDirectory(prefix="memoria-bge-smoke-") as temporary:
        store = Store(Path(temporary) / "smoke.db", "json")
        try:
            engine = MemoryEngine(store, client)
            for passage in passages:
                store.add_memory(passage, "fact", source="offline-smoke")
            reindex = await engine.reindex()
            assert reindex["indexed"] == len(passages) and reindex["remaining"] == 0
            for question, expected in [(query, passages[0]), ("我的猫叫什么名字？", passages[1])]:
                started = time.perf_counter()
                results = await engine.retrieve(question, limit=3, expand_graph=False)
                assert results and results[0]["content"] == expected
                assert results[0]["retrieval"]["vector_rank"] == 1
                retrieval.append({"query": question, "top_content": results[0]["content"],
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                    "retrieval": results[0]["retrieval"]})
        finally:
            store.close()

    encoder = get_encoder(str(model_dir), args.device, client.namespace)
    device = encoder.public_status()["device"]
    manifest_file = model_dir / "download_manifest.json"
    manifest = json.loads(manifest_file.read_text(encoding="utf-8")) if manifest_file.exists() else {}
    base_site = (Path(sys.base_prefix) / "Lib" / "site-packages").resolve()
    inherited_base_packages = any(Path(p).resolve() == base_site for p in sys.path if p)
    return {
        "ok": True, "kind": "offline-smoke-only", "llm_api_calls": 0,
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version.split()[0], "python_executable": sys.executable,
        "venv": sys.prefix != sys.base_prefix, "inherits_base_site_packages": inherited_base_packages,
        "torch": torch.__version__, "transformers": importlib.metadata.version("transformers"),
        "device": device, "gpu": torch.cuda.get_device_name(0) if device == "cuda" else None,
        "model_directory": str(model_dir), "requested_revision": manifest.get("requested_revision"),
        "namespace": client.namespace, "dimension": 512, "max_tokens": MAX_LENGTH,
        "model_parameters": sum(parameter.numel() for parameter in encoder._model.parameters()),
        "policy": POLICY, "query_instruction": QUERY_INSTRUCTION,
        "cold_batch_ms_including_load": round(cold_ms, 3), "document_batch_size": len(passages),
        "warm_query_samples": len(elapsed), "warm_query_median_ms": round(statistics.median(elapsed), 3),
        "warm_query_min_ms": round(min(elapsed), 3), "warm_query_max_ms": round(max(elapsed), 3),
        "max_norm_error": max(abs(norm - 1) for norm in norms),
        "query_document_similarities": similarities,
        "gpu_peak_allocated_mib": round(torch.cuda.max_memory_allocated() / 1024**2, 2) if device == "cuda" else None,
        "reindex": reindex, "retrieval": retrieval,
        "limitation": "Four synthetic Chinese memories and two queries; not a quality benchmark or latency SLO.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=("cuda", "cpu", "auto"), default="cuda")
    parser.add_argument("--model-dir", type=Path, default=ROOT / "model" / "bge-small-zh-v1.5")
    parser.add_argument("--rounds", type=int, default=8)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("--rounds must be positive")
    result = asyncio.run(check(args))
    result["api_model_test"] = check_api(args.model_dir.expanduser().resolve(), args.device)
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized)


if __name__ == "__main__":
    main()
