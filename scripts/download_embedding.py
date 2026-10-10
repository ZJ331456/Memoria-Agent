"""Download a public ModelScope embedding snapshot and verify every file.

Usage (from any working directory):
    python scripts/download_embedding.py
    python scripts/download_embedding.py --revision master

ModelScope 1.34 snapshot_download accepts branches/tags, not raw commits.
For a commit revision this CLI resolves a public ref with the identical allowed
file manifest, downloads through that ref, and verifies the pinned commit's
SHA256/size metadata. It refuses a commit that no public ref currently matches.
Only data/configuration files are downloaded; model code is never executed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path, PurePosixPath
from typing import Any


DEFAULT_MODEL = "BAAI/bge-small-zh-v1.5"
DEFAULT_REVISION = "8399f11f8da998fe932df2684586c92024219d05"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCAL_DIR = PROJECT_ROOT / "model" / "bge-small-zh-v1.5"
CONFIG_FILES = {
    "README.md", "config.json", "configuration.json", "modules.json",
    "config_sentence_transformers.json", "sentence_bert_config.json",
    "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json",
    "added_tokens.json", "vocab.txt", "vocab.json", "merges.txt",
    "tokenizer.model", "spiece.model", "sentencepiece.bpe.model",
}


def allowed_file(path: str) -> bool:
    """Use an explicit data-only allowlist, including pooling configuration."""
    relative = PurePosixPath(path)
    if relative.is_absolute() or ".." in relative.parts or ":" in path or "\\" in path:
        raise ValueError(f"Unsafe remote path: {path!r}")
    return (
        relative.name in CONFIG_FILES
        or relative.suffix == ".safetensors"
        or relative.name.endswith(".safetensors.index.json")
    )


def selected_files(api: Any, model_id: str, revision: str) -> list[dict[str, Any]]:
    result = []
    for item in api.get_model_files(
        model_id, revision=revision, recursive=True, use_cookies=False
    ):
        if item.get("Type") != "blob" or not allowed_file(item["Path"]):
            continue
        digest = item.get("Sha256", "").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"No remote SHA256 for {item['Path']}")
        result.append({
            "path": item["Path"],
            "sha256": digest,
            "size": int(item["Size"]),
            "file_revision": item.get("Revision"),
        })
    result.sort(key=lambda item: item["path"])
    paths = [item["path"] for item in result]
    if len(paths) != len(set(paths)):
        raise ValueError("Duplicate paths in the remote file manifest")
    if not any(path.endswith(".safetensors") for path in paths):
        raise ValueError("This revision contains no safetensors weights")
    if "config.json" not in paths or "tokenizer_config.json" not in paths:
        raise ValueError("This revision is missing model/tokenizer configuration")
    return result


def fingerprint(files: list[dict[str, Any]]) -> list[tuple[str, str, int]]:
    return [(item["path"], item["sha256"], item["size"]) for item in files]


def resolve_revision(
    api: Any, model_id: str, requested: str
) -> tuple[str, list[dict[str, Any]], str]:
    branches, tags = api.get_model_branches_and_tags_details(
        model_id, use_cookies=False
    )
    refs = [item["Revision"] for item in tags + branches]
    if requested in refs:
        return requested, selected_files(api, model_id, requested), "branch_or_tag"
    if not re.fullmatch(r"[0-9a-fA-F]{40}", requested):
        raise ValueError(f"Unknown revision {requested!r}; public refs: {refs}")
    pinned = selected_files(api, model_id, requested)
    for ref in refs:
        if fingerprint(selected_files(api, model_id, ref)) == fingerprint(pinned):
            return ref, pinned, "commit_content_verified_via_public_ref"
    raise ValueError(
        "snapshot_download cannot download raw commits in this SDK, and no "
        "public branch/tag matches the pinned commit's allowed file manifest"
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-id", default=DEFAULT_MODEL)
    parser.add_argument(
        "--revision", help="Public branch/tag or commit SHA (default: pinned BGE commit)"
    )
    parser.add_argument("--local-dir", type=Path, default=DEFAULT_LOCAL_DIR)
    parser.add_argument("--max-workers", type=int, default=4)
    args = parser.parse_args(argv)
    if args.max_workers < 1:
        parser.error("--max-workers must be positive")
    requested = args.revision or (
        DEFAULT_REVISION if args.model_id == DEFAULT_MODEL else "master"
    )
    target = args.local_dir.expanduser().resolve()

    # Import lazily so --help also works without the optional download SDK.
    import modelscope
    from modelscope import snapshot_download
    from modelscope.hub.api import HubApi

    api = HubApi()
    download_ref, expected, pinning_mode = resolve_revision(api, args.model_id, requested)
    paths = [item["path"] for item in expected]
    print(f"Source: https://www.modelscope.cn/models/{args.model_id}", flush=True)
    print(f"Requested revision: {requested}; SDK download ref: {download_ref}", flush=True)
    print(f"Selected {len(paths)} files, {sum(item['size'] for item in expected):,} bytes", flush=True)
    target.mkdir(parents=True, exist_ok=True)
    downloaded = Path(snapshot_download(
        model_id=args.model_id,
        revision=download_ref,
        local_dir=str(target),
        cache_dir=str(target / ".modelscope-cache"),
        cookies=CookieJar(),  # Public download; do not load local credentials.
        allow_patterns=paths,
        ignore_patterns=["*.bin", "*.pt", "*.pth", "*.py", "*.ipynb"],
        max_workers=args.max_workers,
    )).resolve()
    if downloaded != target:
        raise RuntimeError(f"Unexpected SDK output directory: {downloaded}")
    current = selected_files(api, args.model_id, download_ref)
    if fingerprint(current) != fingerprint(expected):
        raise RuntimeError("Remote ref changed during download; manifest was not published")

    verified = []
    for item in expected:
        path = target.joinpath(*PurePosixPath(item["path"]).parts)
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(target):
            raise RuntimeError(f"Missing or unsafe local file: {item['path']}")
        size = path.stat().st_size
        digest = file_sha256(path)
        if size != item["size"] or digest != item["sha256"]:
            raise RuntimeError(f"Remote SHA256/size mismatch: {item['path']}")
        verified.append({**item, "local_sha256": digest, "local_size": size, "verified": True})
        print(f"Verified {item['path']} ({size:,} bytes)", flush=True)

    manifest = {
        "schema_version": 1,
        "source": "modelscope",
        "source_url": f"https://www.modelscope.cn/models/{args.model_id}",
        "model_id": args.model_id,
        "requested_revision": requested,
        "download_revision": download_ref,
        "revision_pinning": pinning_mode,
        "verification": "all selected files match remote SHA256 and byte size",
        "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
        "modelscope_version": modelscope.__version__,
        "local_dir": str(target),
        "total_size": sum(item["size"] for item in verified),
        "downloaded_remote_code": False,
        "excluded_weights": ["*.bin", "*.pt", "*.pth"],
        "files": verified,
    }
    destination = target / "download_manifest.json"
    temporary = target / "download_manifest.json.tmp"
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)
    print(f"Verified download complete: {destination}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Download failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
