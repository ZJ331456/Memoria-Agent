"""Start a Windows-friendly OpenAI-compatible local LLM server.

Example:
  .\\.venv\\Scripts\\python.exe scripts/serve_local_llm.py --model model/Qwen3.5-2B --device cuda --port 8080

Then set Memoria:
  [llm.main]
  model = "model/Qwen3.5-2B"
  base_url = "http://127.0.0.1:8080/v1"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description="Memoria local OpenAI-compatible LLM server")
    parser.add_argument("--model", default="model/Qwen3.5-2B", help="Local model directory")
    parser.add_argument("--device", default="cuda", choices=("cuda", "cpu", "auto"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--served-model-name", default=None, help="Optional id exposed in /v1/models")
    parser.add_argument("--warmup", action="store_true", help="Load weights before accepting traffic")
    args = parser.parse_args()

    import uvicorn

    from memoria.llm.backends.process_engine import get_local_llm, model_path
    from memoria.llm.server import create_local_openai_app

    app = create_local_openai_app(
        model=args.model,
        device=args.device,
        served_model_name=args.served_model_name,
    )
    if args.warmup:
        print(f"Warming up {args.model} on {args.device} ...", flush=True)
        engine = get_local_llm(str(model_path(args.model)), args.device)
        import asyncio

        asyncio.run(engine.chat([{"role": "user", "content": "ping"}], None, 8, timeout=300))
        print("Warmup done.", flush=True)

    print(
        f"Serving OpenAI-compatible API at http://{args.host}:{args.port}/v1 "
        f"(model={args.model}, device={args.device})",
        flush=True,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
