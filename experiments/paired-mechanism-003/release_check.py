from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# Prevent this verifier's own local imports from creating cache artifacts before
# the package-cleanliness check runs. Existing caches still fail closed below.
sys.dont_write_bytecode = True

from canary_binding import verify_bound_canary
from common import (KEY_BOUNDARY_REPAIR_SHA256, LINEAGE_SHA256, PARENT_MAP_SHA256, PUBLICATION_COMMITMENT_SHA256, SCORER_FREEZE_SHA256, SCIENTIFIC_HASHES, pretty_json_bytes, sha256_file)

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parents[1]
FROZEN = ROOT / "frozen_scientific"


def main():
    checks = []
    for name, expected in SCIENTIFIC_HASHES.items():
        actual = sha256_file(FROZEN / name)
        if actual != expected:
            raise SystemExit(f"scientific hash mismatch: {name}")
        checks.append({"check": f"scientific_hash:{name}", "status": "PASS", "sha256": actual})

    for name, expected in [
        ("PARENT_MAP_FROZEN_003.json", PARENT_MAP_SHA256),
        ("LINEAGE_001_002_ABORTS_AND_CANARY.json", LINEAGE_SHA256),
        ("KEY_BOUNDARY_REPAIR_003.json", KEY_BOUNDARY_REPAIR_SHA256),
    ]:
        actual = sha256_file(ROOT / name)
        if actual != expected:
            raise SystemExit(f"hash mismatch: {name}")
        checks.append({"check": f"hash:{name}", "status": "PASS", "sha256": actual})

    for name, expected in [
        ("PUBLICATION_COMMITMENT_003.json", PUBLICATION_COMMITMENT_SHA256),
        ("SCORER_FREEZE_003.json", SCORER_FREEZE_SHA256),
    ]:
        actual = sha256_file(ROOT / name)
        if actual != expected:
            raise SystemExit(f"hash mismatch: {name}")
        checks.append({"check": f"hash:{name}", "status": "PASS", "sha256": actual})
    scorer_freeze = json.loads((ROOT / "SCORER_FREEZE_003.json").read_text("utf-8"))
    for rel, expected in scorer_freeze.get("source_files", {}).items():
        if sha256_file(ROOT / rel) != expected:
            raise SystemExit(f"scorer source mismatch: {rel}")
    checks.append({"check": "frozen_scorer_sources", "status": "PASS", "file_count": len(scorer_freeze.get("source_files", {}))})

    canary = verify_bound_canary(ROOT)
    checks.append({"check": "bound_capacity_canary", "status": "PASS", **canary})

    manifest = json.loads((ROOT / "RUNNER_MANIFEST.json").read_text("utf-8"))
    for rel, expected in manifest.get("runner_files", {}).items():
        path = ROOT / rel
        if not path.exists() or sha256_file(path) != expected:
            raise SystemExit(f"runner manifest mismatch: {rel}")
    for rel, expected in manifest.get("workflow_files", {}).items():
        path = REPO_ROOT / rel
        if not path.exists() or sha256_file(path) != expected:
            raise SystemExit(f"workflow manifest mismatch: {rel}")
    checks.append({
        "check": "runner_manifest", "status": "PASS",
        "runner_files": len(manifest.get("runner_files", {})),
        "workflow_files": len(manifest.get("workflow_files", {})),
        "sha256": sha256_file(ROOT / "RUNNER_MANIFEST.json"),
    })

    workflow_text = (REPO_ROOT / ".github/workflows/olp-30pair-003-execution.yml").read_text("utf-8")
    forbidden_key_paths = ("secret_key.bin", "secret-key-material", "--secret-key", "actions/cache", "private/key", "capstone/key")
    leaked = [x for x in forbidden_key_paths if x in workflow_text]
    if leaked:
        raise SystemExit(f"plaintext key boundary violation: {leaked}")
    if workflow_text.count("${{ secrets.OLP_003_KEY_DERIVATION_SECRET }}") != 4:
        raise SystemExit("protected key derivation secret job count mismatch")
    if workflow_text.count('--key-context "${GITHUB_REPOSITORY}@${GITHUB_SHA}#${GITHUB_RUN_ID}"') != 3:
        raise SystemExit("run-bound key derivation context count mismatch")
    validate_section = workflow_text.split("  validate-protected-secret:", 1)[1].split("  assign-once:", 1)[0]
    assign_section = workflow_text.split("  assign-once:", 1)[1].split("  execute-pairs:", 1)[0]
    if "OLP_003_KEY_DERIVATION_SECRET" not in validate_section:
        raise SystemExit("pre-assignment protected-secret validation job missing")
    if "assignment.py" in validate_section or "--key-context" in validate_section or "upload-artifact" in validate_section:
        raise SystemExit("protected-secret validation job exceeds format-check scope")
    if "needs: [pre_run_003, validate-protected-secret]" not in assign_section:
        raise SystemExit("assignment job is not gated on protected-secret validation")
    if "if: github.run_attempt == 1" not in assign_section or "needs.validate-protected-secret.result == 'success'" not in assign_section:
        raise SystemExit("assignment job lacks first-attempt validation-success gate")
    execute_section = workflow_text.split("  execute-pairs:", 1)[1].split("  collect-public:", 1)[0]
    if "if: github.run_attempt == 1 && needs.assign-once.result == 'success'" not in execute_section:
        raise SystemExit("execution matrix lacks first-attempt assignment-success gate")
    blind_section = workflow_text.split("  blind-score-and-capstone-gate:", 1)[1].split("  independently-verify-blind-scores:", 1)[0]
    verify_section = workflow_text.split("  independently-verify-blind-scores:", 1)[1].split("  unblind-once-and-publish:", 1)[0]
    if "OLP_003_KEY_DERIVATION_SECRET" in blind_section or "OLP_003_KEY_DERIVATION_SECRET" in verify_section:
        raise SystemExit("blind scorer or independent verifier receives derivation secret")
    checks.append({
        "check": "protected_key_derivation_boundary",
        "status": "PASS",
        "scheme": "HKDF-SHA256-32-V1",
        "plaintext_key_artifact_created": False,
        "secret_artifact_uploaded": False,
        "secret_format_validated_before_assignment_job": True,
        "failed_validation_leaves_assignment_job_skipped": True,
        "workflow_rerun_cannot_start_assignment_or_pair_execution": True,
        "blind_jobs_receive_secret": False,
    })

    # Syntax check without creating .pyc files.
    py_files = sorted(p for p in ROOT.rglob("*.py") if "__pycache__" not in p.parts)
    for path in py_files:
        ast.parse(path.read_text("utf-8"), filename=str(path))
    checks.append({"check": "python_ast_parse", "status": "PASS", "file_count": len(py_files)})

    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"],
        cwd=ROOT, env=env, text=True, capture_output=True, check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(proc.returncode)
    m = re.search(r"Ran (\d+) tests?", proc.stderr)
    passed = int(m.group(1)) if m else 0
    checks.append({"check": "unit_tests", "status": "PASS", "passed": passed})

    with tempfile.TemporaryDirectory(prefix="olp003-dry-preflight-") as td:
        out = Path(td) / "nested" / "build" / "PREFLIGHT_003_DRY.json"
        dry = subprocess.run(
            [sys.executable, "preflight_003.py", "--out", str(out), "--dry-run-no-external"],
            cwd=ROOT, env=env, text=True, capture_output=True, check=False,
        )
        if dry.returncode != 0:
            print(dry.stdout)
            print(dry.stderr, file=sys.stderr)
            raise SystemExit("003 dry preflight failed")
        obj = json.loads(out.read_text("utf-8"))
        if (
            obj.get("status") != "PREFLIGHT_003_DRY_RUN_PASS"
            or obj.get("real_assignment_created") is not False
            or obj.get("benchmark_model_calls") != 0
            or obj.get("unblinded") is not False
        ):
            raise SystemExit("003 dry preflight semantic mismatch")
        checks.append({"check": "dry_preflight_nested_output", "status": "PASS", "disposition": obj.get("disposition")})

    forbidden = {"secret_key.bin", "condition_map.enc", "blinded_run_manifest.json", "ASSIGNMENT_LOCK.json"}
    present = [str(p.relative_to(ROOT)) for p in ROOT.rglob("*") if p.is_file() and p.name in forbidden]
    if present or (ROOT / "build" / "assignment").exists():
        raise SystemExit(f"real/private assignment artifacts present: {present}")
    checks.append({"check": "real_assignment_absent", "status": "PASS"})

    cache_files = [
        str(p.relative_to(REPO_ROOT)) for p in REPO_ROOT.rglob("*")
        if p.is_file() and (p.suffix == ".pyc" or "__pycache__" in p.parts or ".pytest_cache" in p.parts)
    ]
    if cache_files:
        raise SystemExit(f"cache artifacts present: {cache_files}")
    checks.append({"check": "cache_artifacts_absent", "status": "PASS"})

    report = {
        "schema": "openline.paired-mechanism-benchmark.003-dry-run.v2",
        "experiment_id": "olp-core21-paired-mechanism-003",
        "status": "DRY_RUN_PASS",
        "checks": checks,
        "scientific_content_changed": False,
        "scientific_payload_byte_identical_to_001": True,
        "001_unblinded": False,
        "002_unblinded": False,
        "002_condition_blind_signal_values_inspected_after_retirement": True,
        "condition_linked_predecessor_effect_known": False,
        "003_real_assignment_created": False,
        "benchmark_model_calls": 0,
        "live_capacity_calls_during_release_check": 0,
        "unblinded": False,
        "publish_regardless_capstone_frozen": True,
        "protected_key_derivation_boundary": True,
        "plaintext_key_artifact_created": False,
        "publication_commitment_sha256": PUBLICATION_COMMITMENT_SHA256,
        "scorer_freeze_sha256": SCORER_FREEZE_SHA256,
        "statements": ["REAL_ASSIGNMENT_NOT_CREATED", "BENCHMARK_MODEL_CALLS_0", "UNBLINDED_FALSE", "PLAINTEXT_KEY_ARTIFACT_CREATED_FALSE"],
    }
    path = ROOT / "DRY_RUN_RECEIPT.json"
    path.write_bytes(pretty_json_bytes(report))
    (ROOT / "DRY_RUN_RECEIPT.json.sha256").write_text(f"{sha256_file(path)}  DRY_RUN_RECEIPT.json\n", encoding="utf-8")
    print(json.dumps({"status": "DRY_RUN_PASS", "tests_passed": passed, "receipt": "DRY_RUN_RECEIPT.json"}, indent=2))


if __name__ == "__main__":
    main()
