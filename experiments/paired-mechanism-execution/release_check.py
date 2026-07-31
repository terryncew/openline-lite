from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from common import FROZEN_HASHES, PREFLIGHT_PASS_SHA256, pretty_json_bytes, sha256_file

ROOT = Path(__file__).resolve().parent
FROZEN = ROOT / "frozen"
BUILD = ROOT / "build"


def main():
    checks = []
    for name, expected in FROZEN_HASHES.items():
        actual = sha256_file(FROZEN / name)
        ok = actual == expected
        checks.append({"check": f"frozen_hash:{name}", "status": "PASS" if ok else "FAIL", "actual": actual, "expected": expected})
        if not ok:
            raise SystemExit(f"frozen hash mismatch: {name}")
    side = (FROZEN / "PREFLIGHT_PASS.json.sha256").read_text("utf-8")
    if PREFLIGHT_PASS_SHA256 not in side:
        raise SystemExit("PREFLIGHT_PASS sidecar mismatch")
    checks.append({"check": "preflight_pass_sidecar", "status": "PASS"})

    runner_manifest = json.loads((ROOT / "RUNNER_MANIFEST.json").read_text("utf-8"))
    for rel, expected in runner_manifest.get("files", {}).items():
        target = (ROOT / rel).resolve()
        actual = sha256_file(target)
        if actual != expected:
            raise SystemExit(f"runner manifest mismatch: {rel}")
    checks.append({"check": "runner_manifest", "status": "PASS", "files": len(runner_manifest.get("files", {}))})

    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(proc.returncode)
    passed = sum(1 for line in proc.stderr.splitlines() if line.rstrip().endswith("... ok"))
    checks.append({"check": "unit_tests", "status": "PASS", "passed": passed})

    compile_proc = subprocess.run([sys.executable, "-m", "compileall", "-q", "."], cwd=ROOT, check=False)
    if compile_proc.returncode != 0:
        raise SystemExit("compileall failed")
    checks.append({"check": "compileall", "status": "PASS"})

    # No real-run output tree is permitted in a release/dry-run build.
    real_assignment = BUILD / "assignment"
    if real_assignment.exists():
        raise SystemExit("real assignment output directory exists after dry verification")
    checks.append({"check": "real_assignment_absent", "status": "PASS"})

    report = {
        "schema": "openline.paired-mechanism-benchmark.execution-dry-run.v1",
        "experiment_id": "olp-core21-paired-mechanism-001",
        "status": "DRY_RUN_PASS",
        "checks": checks,
        "scientific_content_changed": False,
        "real_assignment_created": False,
        "benchmark_model_calls": 0,
        "unblinded": False,
        "statements": [
            "REAL_ASSIGNMENT_NOT_CREATED",
            "BENCHMARK_MODEL_CALLS_0",
            "UNBLINDED_FALSE"
        ]
    }
    (ROOT / "DRY_RUN_RECEIPT.json").write_bytes(pretty_json_bytes(report))
    print(json.dumps({"status": "DRY_RUN_PASS", "tests_passed": passed, "receipt": "DRY_RUN_RECEIPT.json"}, indent=2))


if __name__ == "__main__":
    main()
