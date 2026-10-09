"""Download pinned public releases and select exactly five existing questions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx

from .longmemeval import file_digest

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = Path(__file__).with_name("benchmarks.json")


def prepare(name: str, root: Path) -> dict:
    spec = json.loads(REGISTRY.read_text())[name]
    folder = root / name
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        for item in spec["files"]:
            path = folder / item["path"]
            if not path.exists():
                response = client.get(f"{spec['repository'].replace('github.com', 'raw.githubusercontent.com')}/"
                                      f"{spec['revision']}/{item['path']}")
                response.raise_for_status()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(response.content)
            if file_digest(path) != item["sha256"]:
                raise ValueError(f"public dataset digest mismatch: {path}; inspect before replacing")
    cases = []
    if name == "locomo":
        records = {r["sample_id"]: r for r in json.loads((folder / "data/locomo10.json").read_text())}
        for selected in spec["selection"]:
            source = records[selected["sample_id"]]
            cases.append({**selected, "qa": source["qa"][selected["qa_index"]],
                          "conversation": source["conversation"]})
    else:
        for selected in spec["selection"]:
            directory = folder / "bench/data" / selected["domain"]
            checkpoints = [json.loads(line) for line in (directory / "checkpoints.jsonl").read_text().splitlines()]
            checkpoint = next(c for c in checkpoints if c["checkpoint_id"] == selected["checkpoint_id"])
            episodes = [json.loads(line) for line in (directory / "episodes.jsonl").read_text().splitlines()]
            episode = next(e for e in episodes if e["episode_id"] == checkpoint["episode_id"])
            cases.append({"domain": selected["domain"], "episode": episode, "checkpoint": checkpoint})
    payload = {"source": spec, "selection_protocol": "five fixed published IDs; no local question or label generation",
               "cases": cases}
    path = folder / "subset5.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return {"dataset": name, "path": str(path), "cases": len(cases), "sha256": file_digest(path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=["locomo", "gatemem", "all"], default="all")
    parser.add_argument("--root", type=Path, default=ROOT / "data/benchmark")
    args = parser.parse_args()
    for name in (["locomo", "gatemem"] if args.dataset == "all" else [args.dataset]):
        print(json.dumps(prepare(name, args.root), ensure_ascii=False))


if __name__ == "__main__":
    main()
