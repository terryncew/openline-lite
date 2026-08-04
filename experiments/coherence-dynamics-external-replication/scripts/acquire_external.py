from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from huggingface_hub import hf_hub_download

from external_replication.canonical import sha256_file, write_json


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--expected-sha256", required=True)
    arguments = parser.parse_args()
    output = Path(arguments.output)
    output.mkdir(parents=True, exist_ok=True)
    downloaded = Path(
        hf_hub_download(
            repo_id="thoughtworks/agentic-coding-trajectories",
            repo_type="dataset",
            filename="sessions.parquet",
            revision=arguments.revision,
            local_dir=output,
        )
    )
    destination = output / "sessions.parquet"
    if downloaded.resolve() != destination.resolve():
        destination.write_bytes(downloaded.read_bytes())
    digest = sha256_file(destination)
    if digest != arguments.expected_sha256:
        raise SystemExit(
            f"external file hash mismatch: {digest} != {arguments.expected_sha256}"
        )
    manifest = {
        "schema": "coherence-dynamics.external-replication.data-manifest.v2",
        "repo_id": "thoughtworks/agentic-coding-trajectories",
        "requested_revision": arguments.revision,
        "file": "sessions.parquet",
        "bytes": destination.stat().st_size,
        "sha256": digest,
        "expected_sha256": arguments.expected_sha256,
        "created_at_utc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "model_api_calls": 0,
        "api_credit_spend_usd": 0.0,
    }
    write_json(output / "DATA_MANIFEST.json", manifest)
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
