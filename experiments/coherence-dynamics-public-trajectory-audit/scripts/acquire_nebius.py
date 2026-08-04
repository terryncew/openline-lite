#!/usr/bin/env python3
"""Explicitly networked public-data acquisition. This never calls a model API."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--revision", default="a8a64e5", help="Pinned dataset revision/data commit")
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    repo_id = "nebius/SWE-agent-trajectories"
    info = HfApi().dataset_info(repo_id=repo_id, revision=args.revision)
    resolved_revision = info.sha
    if not isinstance(resolved_revision, str) or not resolved_revision.startswith(args.revision):
        raise SystemExit(f"revision drift: requested {args.revision}, resolved {resolved_revision}")

    snapshot = Path(
        snapshot_download(
            repo_id=repo_id,
            repo_type="dataset",
            revision=resolved_revision,
            allow_patterns=["data/*.parquet", "README.md"],
            local_dir=output,
        )
    )
    parquet_files = sorted(snapshot.glob("data/train-*.parquet"))
    if len(parquet_files) != 12:
        raise SystemExit(f"expected exactly 12 data shards, found {len(parquet_files)}")
    files = sorted(path for path in snapshot.rglob("*") if path.is_file() and ".cache" not in path.parts)
    manifest = {
        "schema": "coherence-dynamics.public-data-acquisition.v2",
        "created_at_utc": utc_now(),
        "repo_id": repo_id,
        "requested_revision": args.revision,
        "resolved_revision": resolved_revision,
        "resolved_snapshot_path": str(snapshot),
        "parquet_shard_count": len(parquet_files),
        "files": {
            str(path.relative_to(snapshot)): {"bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in files
        },
        "model_api_calls": 0,
        "api_credit_spend_usd": 0.0,
        "expected_license": "CC-BY-4.0; underlying repository and model-output licenses also apply",
    }
    (output / "DATA_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
