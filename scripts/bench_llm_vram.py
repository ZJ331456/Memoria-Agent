"""Measure GPU VRAM for local LLM models under load + batched generate."""

from __future__ import annotations

import argparse
import gc
import json
import time
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODELS = [
    "LFM2.5-1.2B-Instruct",
    "Qwen3.5-0.8B",
    "Qwen3.5-2B",
]


def gib(n: int | float) -> float:
    return float(n) / (1024**3)


def mib(n: int | float) -> float:
    return float(n) / (1024**2)


def reset_cuda() -> None:
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()
        torch.cuda.reset_peak_memory_stats()
        torch.cuda.synchronize()


def mem_snapshot(tag: str) -> dict[str, float]:
    allocated = torch.cuda.memory_allocated()
    reserved = torch.cuda.memory_reserved()
    peak_alloc = torch.cuda.max_memory_allocated()
    peak_reserved = torch.cuda.max_memory_reserved()
    free, total = torch.cuda.mem_get_info()
    snap = {
        "tag": tag,
        "allocated_mib": round(mib(allocated), 1),
        "reserved_mib": round(mib(reserved), 1),
        "peak_allocated_mib": round(mib(peak_alloc), 1),
        "peak_reserved_mib": round(mib(peak_reserved), 1),
        "free_mib": round(mib(free), 1),
        "total_mib": round(mib(total), 1),
        "used_driver_mib": round(mib(total - free), 1),
    }
    print(
        f"  [{tag}] alloc={snap['allocated_mib']:.1f} MiB  "
        f"reserved={snap['reserved_mib']:.1f} MiB  "
        f"peak_alloc={snap['peak_allocated_mib']:.1f} MiB  "
        f"driver_used={snap['used_driver_mib']:.1f}/{snap['total_mib']:.1f} MiB",
        flush=True,
    )
    return snap


def build_batch(tokenizer, batch_size: int, prompt: str, device: torch.device):
    texts = [prompt] * batch_size
    enc = tokenizer(texts, return_tensors="pt", padding=True, truncation=True, max_length=256)
    return {k: v.to(device) for k, v in enc.items()}


def bench_one(model_dir: Path, batch_sizes: list[int], max_new_tokens: int, dtype: torch.dtype) -> dict:
    name = model_dir.name
    print(f"\n=== {name} ===", flush=True)
    reset_cuda()
    baseline = mem_snapshot("baseline_before_load")

    t0 = time.perf_counter()
    tokenizer = AutoTokenizer.from_pretrained(str(model_dir), trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        str(model_dir),
        dtype=dtype,
        trust_remote_code=True,
        low_cpu_mem_usage=True,
    )
    model.to("cuda")
    model.eval()
    torch.cuda.synchronize()
    load_s = time.perf_counter() - t0
    after_load = mem_snapshot("after_load")
    print(f"  load_time={load_s:.1f}s  dtype={dtype}", flush=True)

    prompt = "用一句话介绍你自己。"
    batch_results = []
    oom_at = None

    for bs in batch_sizes:
        reset_cuda()
        # keep model weights; only reset peak stats for this trial
        torch.cuda.reset_peak_memory_stats()
        try:
            device = next(model.parameters()).device
            inputs = build_batch(tokenizer, bs, prompt, device)
            t1 = time.perf_counter()
            with torch.inference_mode():
                out = model.generate(
                    **inputs,
                    max_new_tokens=max_new_tokens,
                    do_sample=False,
                    pad_token_id=tokenizer.pad_token_id,
                )
            torch.cuda.synchronize()
            gen_s = time.perf_counter() - t1
            snap = mem_snapshot(f"generate_bs{bs}_tok{max_new_tokens}")
            tokens = int(out.numel())
            batch_results.append(
                {
                    "batch_size": bs,
                    "max_new_tokens": max_new_tokens,
                    "ok": True,
                    "seconds": round(gen_s, 3),
                    "output_tensor_elems": tokens,
                    **{k: snap[k] for k in (
                        "allocated_mib",
                        "reserved_mib",
                        "peak_allocated_mib",
                        "peak_reserved_mib",
                        "used_driver_mib",
                    )},
                }
            )
            del inputs, out
        except torch.cuda.OutOfMemoryError as exc:
            print(f"  [OOM] batch_size={bs}: {exc}", flush=True)
            oom_at = bs
            batch_results.append({"batch_size": bs, "ok": False, "error": "CUDA OOM"})
            reset_cuda()
            break

    # unload
    del model, tokenizer
    reset_cuda()
    after_unload = mem_snapshot("after_unload")

    return {
        "model": name,
        "path": str(model_dir),
        "dtype": str(dtype).replace("torch.", ""),
        "load_seconds": round(load_s, 2),
        "baseline_driver_used_mib": baseline["used_driver_mib"],
        "weights_peak_allocated_mib": after_load["peak_allocated_mib"],
        "weights_driver_used_mib": after_load["used_driver_mib"],
        "weights_only_approx_mib": round(
            after_load["allocated_mib"], 1
        ),
        "batches": batch_results,
        "oom_batch_size": oom_at,
        "after_unload_driver_used_mib": after_unload["used_driver_mib"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="*", default=DEFAULT_MODELS)
    parser.add_argument("--batch-sizes", nargs="*", type=int, default=[1, 2, 4, 8])
    parser.add_argument("--max-new-tokens", type=int, default=64)
    parser.add_argument(
        "--dtype",
        choices=["bfloat16", "float16"],
        default="bfloat16",
        help="Weights dtype on GPU",
    )
    args = parser.parse_args()
    dtype = torch.bfloat16 if args.dtype == "bfloat16" else torch.float16

    if not torch.cuda.is_available():
        raise SystemExit("CUDA not available")

    print(f"GPU: {torch.cuda.get_device_name(0)}", flush=True)
    free, total = torch.cuda.mem_get_info()
    print(
        f"CUDA mem_get_info: free={mib(free):.0f} MiB / total={mib(total):.0f} MiB "
        f"(~{gib(free):.2f}/{gib(total):.2f} GiB)",
        flush=True,
    )
    print(f"batch_sizes={args.batch_sizes} max_new_tokens={args.max_new_tokens} dtype={args.dtype}", flush=True)

    results = []
    for name in args.models:
        model_dir = PROJECT_ROOT / "model" / name
        if not model_dir.is_dir():
            print(f"SKIP missing: {model_dir}", flush=True)
            continue
        try:
            results.append(bench_one(model_dir, args.batch_sizes, args.max_new_tokens, dtype))
        except Exception as exc:
            print(f"FAIL {name}: {type(exc).__name__}: {exc}", flush=True)
            reset_cuda()
            results.append({"model": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"})

    out_path = PROJECT_ROOT / "model" / "vram_bench_results.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}", flush=True)

    print("\n======== SUMMARY ========", flush=True)
    for r in results:
        if r.get("error") and "batches" not in r:
            print(f"{r['model']}: FAILED {r['error']}", flush=True)
            continue
        print(
            f"{r['model']}: weights≈{r['weights_only_approx_mib']:.0f} MiB  "
            f"driver_after_load≈{r['weights_driver_used_mib']:.0f} MiB  "
            f"load={r['load_seconds']}s",
            flush=True,
        )
        for b in r.get("batches", []):
            if b.get("ok"):
                print(
                    f"  bs={b['batch_size']}: peak_alloc={b['peak_allocated_mib']:.0f} MiB  "
                    f"peak_reserved={b['peak_reserved_mib']:.0f} MiB  "
                    f"driver={b['used_driver_mib']:.0f} MiB  {b['seconds']}s",
                    flush=True,
                )
            else:
                print(f"  bs={b['batch_size']}: OOM", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
