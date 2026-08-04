from __future__ import annotations

import argparse
import json
from pathlib import Path

from external_replication.canonical import sha256_file


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--lock", required=True)
    parser.add_argument("--recovery", required=True)
    args = parser.parse_args()

    profile_path = Path(args.profile)
    receipt_path = Path(args.receipt)
    lock_path = Path(args.lock)
    recovery_path = Path(args.recovery)
    profile = json.loads(profile_path.read_text())
    receipt = json.loads(receipt_path.read_text())

    if receipt["status"] != "SOURCE_PROFILE_RECOVERED_AND_SEALED":
        raise SystemExit("source recovery receipt is not sealed")
    if receipt["profile_sha256"] != sha256_file(profile_path):
        raise SystemExit("recovered profile hash mismatch")
    if receipt["source_profile_lock_sha256"] != sha256_file(lock_path):
        raise SystemExit("source lock binding mismatch")
    if receipt["source_profile_recovery_sha256"] != sha256_file(recovery_path):
        raise SystemExit("source recovery protocol binding mismatch")
    if receipt["external_dataset_acquired"] or receipt["external_rows_scored"] != 0:
        raise SystemExit("source profile was not sealed before external access")
    if profile["profile_kind"] != "SOURCE_RECOVERED_AND_SEALED_BEFORE_EXTERNAL_ACQUISITION":
        raise SystemExit("unexpected source profile kind")
    for family in ("simple", "simple_cd"):
        if profile["families"][family]["metric_identity_status"] != "NUMERICALLY_EQUIVALENT_NOT_BITWISE_MODEL_IDENTITY":
            raise SystemExit(f"{family} source recovery status invalid")
    print(json.dumps({"status": "PASS", "profile_sha256": receipt["profile_sha256"]}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
