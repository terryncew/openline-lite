from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from canary_binding import verify_bound_canary
from common import (
    ALLOWED_TOOLS,
    BENCHMARK_REVISION,
    CAPACITY_CANARY_RECEIPT_SHA256,
    EXPERIMENT_ID,
    GLOBAL_MIN_REQUEST_START_INTERVAL_SECONDS,
    LINEAGE_SHA256,
    MAX_OUTPUT_TOKENS,
    MAX_TOOL_CALLS,
    MAX_WALL_SECONDS,
    PAIR_CONTROLLED_WORST_CASE_SECONDS,
    PAIR_JOB_TIMEOUT_SECONDS,
    PAIR_MATRIX_MAX_PARALLEL,
    PARENT_MAP_SHA256,
    PUBLICATION_COMMITMENT_SHA256,
    SCORER_FREEZE_SHA256,
    PINNED_MODEL,
    REASONING_EFFORT,
    SCIENTIFIC_HASHES,
    SHELL_TIMEOUT_SECONDS,
    SOURCE_SCIENTIFIC_EXPERIMENT_ID,
    load_json,
    pretty_json_bytes,
    sha256_file,
)

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parents[1]
FROZEN = ROOT / "frozen_scientific"


def run(cmd, *, cwd=None, timeout=300):
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, timeout=timeout, check=False)


def seal(obj: dict, out: Path):
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(pretty_json_bytes(obj))
    digest = sha256_file(out)
    (out.parent / f"{out.name}.sha256").write_text(f"{digest}  {out.name}\n", encoding="utf-8")
    return digest


def blocked(receipt: dict, stage: str, reason: str, out: Path, detail=None):
    receipt.update({
        "status": "PREFLIGHT_003_BLOCKED", "failed_stage": stage,
        "failure_reason": reason, "real_assignment_created": False,
        "benchmark_model_calls": 0, "unblinded": False,
    })
    if detail is not None:
        receipt["failure_detail"] = detail
    target = out.with_name("PREFLIGHT_003_BLOCKED.json")
    digest = seal(receipt, target)
    print(json.dumps({"status": receipt["status"], "receipt": str(target), "sha256": digest}, indent=2))
    raise SystemExit(1)


def verify_hashes(receipt: dict, out: Path):
    rows = []
    for name, expected in SCIENTIFIC_HASHES.items():
        p = FROZEN / name
        if not p.exists():
            blocked(receipt, "scientific_hashes", f"missing inherited scientific artifact: {name}", out)
        actual = sha256_file(p)
        rows.append({"file": name, "expected": expected, "actual": actual, "status": "PASS" if actual == expected else "FAIL"})
        if actual != expected:
            blocked(receipt, "scientific_hashes", f"hash mismatch: {name}", out, rows)
    fixed = [
        ("PARENT_MAP_FROZEN_003.json", PARENT_MAP_SHA256),
        ("LINEAGE_001_002_ABORTS_AND_CANARY.json", LINEAGE_SHA256),
        ("CAPACITY_CANARY_PASS_BOUND.json", CAPACITY_CANARY_RECEIPT_SHA256),
        ("PUBLICATION_COMMITMENT_003.json", PUBLICATION_COMMITMENT_SHA256),
        ("SCORER_FREEZE_003.json", SCORER_FREEZE_SHA256),
    ]
    for name, expected in fixed:
        if sha256_file(ROOT / name) != expected:
            blocked(receipt, "execution_lineage_hashes", f"hash mismatch: {name}", out)
    scorer_freeze = load_json(ROOT / "SCORER_FREEZE_003.json")
    for rel, expected in (scorer_freeze.get("source_files") or {}).items():
        source = ROOT / rel
        if not source.exists() or sha256_file(source) != expected:
            blocked(receipt, "scorer_freeze", f"frozen scorer source mismatch: {rel}", out)
    publication = load_json(ROOT / "PUBLICATION_COMMITMENT_003.json")
    if publication.get("publication_required") is not True or publication.get("same_design_rerun_authorized") is not False:
        blocked(receipt, "publication_commitment", "publish-regardless commitment semantic mismatch", out)
    receipt["publication_commitment"] = {"status": "PASS", "sha256": PUBLICATION_COMMITMENT_SHA256, "publish_regardless": True}
    receipt["scorer_freeze"] = {"status": "PASS", "sha256": SCORER_FREEZE_SHA256, "independent_recomputation_required": True}
    receipt["scientific_hashes"] = rows
    receipt["scientific_payload_byte_identical_to_001_and_002"] = True


def verify_pair_config(receipt: dict, out: Path):
    pair = load_json(FROZEN / "PAIR_SET_FROZEN.json")
    if pair.get("experiment_id") != SOURCE_SCIENTIFIC_EXPERIMENT_ID:
        blocked(receipt, "pair_set", "inherited pair set source identity mismatch", out)
    pairs = pair.get("pairs", [])
    if len(pairs) != 30 or [p.get("pair_id") for p in pairs] != [f"P{i:02d}" for i in range(1, 31)]:
        blocked(receipt, "pair_set", "exact 30 inherited pair records not present", out)
    cfg = pair.get("common_execution_config", {})
    expected = {
        "model": PINNED_MODEL, "reasoning_effort": REASONING_EFFORT,
        "max_output_tokens": MAX_OUTPUT_TOKENS, "max_tool_calls": MAX_TOOL_CALLS,
        "max_wall_seconds": MAX_WALL_SECONDS, "shell_timeout_seconds": SHELL_TIMEOUT_SECONDS,
        "external_memory": "disabled", "network": "disabled",
    }
    for k, v in expected.items():
        if cfg.get(k) != v:
            blocked(receipt, "execution_config", f"inherited frozen config mismatch: {k}", out, {"expected": v, "actual": cfg.get(k)})
    if set((cfg.get("tools") or {}).keys()) != set(ALLOWED_TOOLS):
        blocked(receipt, "execution_config", "tool allowlist mismatch", out)
    receipt["execution_config"] = {"status": "PASS", "scientific_payload_inherited": True, "pinned": expected, "tools": list(ALLOWED_TOOLS)}


def verify_runner_manifest(receipt: dict, out: Path):
    path = ROOT / "RUNNER_MANIFEST.json"
    if not path.exists():
        blocked(receipt, "runner_manifest", "RUNNER_MANIFEST missing", out)
    manifest = load_json(path)
    for rel, expected in manifest.get("runner_files", {}).items():
        p = ROOT / rel
        if not p.exists() or sha256_file(p) != expected:
            blocked(receipt, "runner_manifest", f"runner file mismatch: {rel}", out)
    for rel, expected in manifest.get("workflow_files", {}).items():
        p = REPO_ROOT / rel
        if not p.exists() or sha256_file(p) != expected:
            blocked(receipt, "runner_manifest", f"workflow file mismatch: {rel}", out)
    receipt["runner_manifest"] = {
        "status": "PASS", "sha256": sha256_file(path),
        "runner_file_count": len(manifest.get("runner_files", {})),
        "workflow_file_count": len(manifest.get("workflow_files", {})),
    }


def verify_runtime_bound(receipt: dict, out: Path):
    if PAIR_MATRIX_MAX_PARALLEL != 1:
        blocked(receipt, "runtime_bound", "pair matrix max_parallel is not 1", out)
    if PAIR_CONTROLLED_WORST_CASE_SECONDS >= PAIR_JOB_TIMEOUT_SECONDS:
        blocked(receipt, "runtime_bound", "controlled worst-case pair bound reaches/exceeds job timeout", out)
    receipt["runtime_bound"] = {
        "status": "PASS", "matrix_max_parallel": PAIR_MATRIX_MAX_PARALLEL,
        "global_minimum_request_start_interval_seconds": GLOBAL_MIN_REQUEST_START_INTERVAL_SECONDS,
        "controlled_worst_case_pair_seconds": PAIR_CONTROLLED_WORST_CASE_SECONDS,
        "pair_job_timeout_seconds": PAIR_JOB_TIMEOUT_SECONDS,
        "margin_seconds": PAIR_JOB_TIMEOUT_SECONDS - PAIR_CONTROLLED_WORST_CASE_SECONDS,
        "pacing_and_retry_wait_excluded_from_scientific_wall_budget": True,
    }


def verify_network_sandbox(receipt: dict, out: Path):
    required = ["git", "python3", "bash", "sudo", "unshare", "setpriv"]
    missing = [b for b in required if shutil.which(b) is None]
    if missing:
        blocked(receipt, "network_sandbox", "required binaries missing", out, missing)
    probe = "python3 - <<'PY'\nimport socket,sys\ntry:\n socket.create_connection(('github.com',443),timeout=2)\nexcept OSError:\n sys.exit(0)\nsys.exit(9)\nPY"
    p = run(["sudo", "unshare", "--net", "--pid", "--fork", "--mount-proc", "setpriv", f"--reuid={os.getuid()}", f"--regid={os.getgid()}", "--clear-groups", "bash", "-lc", probe], timeout=20)
    if p.returncode != 0:
        blocked(receipt, "network_sandbox", "agent run_shell network isolation unavailable", out, {"returncode": p.returncode})
    receipt["network_sandbox"] = {"status": "PASS", "agent_tool_network": "DENIED"}


def verify_checkouts(receipt: dict, out: Path):
    pairs = load_json(FROZEN / "PAIR_SET_FROZEN.json")["pairs"]
    parent = load_json(ROOT / "PARENT_MAP_FROZEN_003.json")["pairs"]
    tmp = Path(tempfile.mkdtemp(prefix="olp003-preflight-checkouts-"))
    rows = []
    try:
        for idx, pair in enumerate(pairs, 1):
            pid = pair["pair_id"]
            row = parent.get(pid) or {}
            expected = row.get("resolved_parent_sha")
            if row.get("task_commit_sha") != pair.get("task_commit_sha") or row.get("checkpoint_ref") != pair.get("checkpoint_ref"):
                blocked(receipt, "git_parent_checkouts", f"parent-map binding mismatch: {pid}", out)
            d = tmp / pid
            d.mkdir()
            if run(["git", "init", "-q"], cwd=d).returncode != 0:
                blocked(receipt, "git_parent_checkouts", f"git init failed: {pid}", out)
            run(["git", "remote", "add", "origin", f'https://github.com/{pair["repository"]}.git'], cwd=d)
            f = run(["git", "fetch", "-q", "--depth=1", "--no-tags", "origin", expected], cwd=d, timeout=180)
            if f.returncode != 0:
                blocked(receipt, "git_parent_checkouts", f"exact parent fetch failed: {pid}", out, {"returncode": f.returncode})
            co = run(["git", "checkout", "-q", "--detach", expected], cwd=d, timeout=60)
            head = run(["git", "rev-parse", "HEAD"], cwd=d)
            if co.returncode != 0 or head.stdout.strip() != expected:
                blocked(receipt, "git_parent_checkouts", f"exact parent checkout failed: {pid}", out)
            rows.append({"pair_id": pid, "repository": pair["repository"], "resolved_parent_sha": expected, "status": "PASS"})
            print(f"003_CHECKOUT_PASS {idx:02d}/30 {pid} {expected}", flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    receipt["git_parent_checkouts"] = rows
    receipt["git_parent_checkout_summary"] = "30/30 PASS"


def run_preflight(*, out: Path, perform_checkouts: bool = True, perform_network_sandbox: bool = True):
    receipt = {
        "schema": "openline.paired-mechanism-benchmark.003-preflight.v1",
        "experiment_id": EXPERIMENT_ID,
        "benchmark_revision": BENCHMARK_REVISION,
        "lineage_statement": "fresh paced rerun following blinded infrastructure-aborted 001 and 002; 002 condition-blind partial signal values were inspected after retirement, but no predecessor condition mapping, directional effect, key, assignment, or trace is reused.",
        "real_assignment_created": False, "benchmark_model_calls": 0, "unblinded": False,
        "live_capacity_calls_in_003_preflight": 0,
        "condition_blind_002_signal_values_previously_inspected": True,
        "condition_linked_predecessor_effect_known": False,
        "publish_regardless_capstone_frozen": True,
        "publication_commitment_sha256": PUBLICATION_COMMITMENT_SHA256,
        "scorer_freeze_sha256": SCORER_FREEZE_SHA256,
    }
    verify_hashes(receipt, out)
    verify_pair_config(receipt, out)
    verify_runner_manifest(receipt, out)
    verify_runtime_bound(receipt, out)
    try:
        receipt["bound_capacity_canary"] = verify_bound_canary(ROOT)
    except Exception as exc:
        blocked(receipt, "capacity_canary_binding", str(exc), out)
    if perform_network_sandbox:
        verify_network_sandbox(receipt, out)
    else:
        receipt["network_sandbox"] = {"status": "DRY_RUN_NOT_EXECUTED"}
    if perform_checkouts:
        verify_checkouts(receipt, out)
    else:
        receipt["git_parent_checkout_summary"] = "DRY_RUN_NOT_EXECUTED"
    if perform_checkouts and perform_network_sandbox:
        receipt["status"] = "PREFLIGHT_003_PASS"
        receipt["disposition"] = "CLEARED_FOR_SEPARATE_FRESH_003_ASSIGNMENT_STEP"
    else:
        receipt["status"] = "PREFLIGHT_003_DRY_RUN_PASS"
        receipt["disposition"] = "NOT_CLEARED_NO_EXTERNAL_CHECKOUT_OR_SANDBOX"
    digest = seal(receipt, out)
    return receipt, digest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry-run-no-external", action="store_true")
    args = ap.parse_args()
    receipt, digest = run_preflight(
        out=Path(args.out),
        perform_checkouts=not args.dry_run_no_external,
        perform_network_sandbox=not args.dry_run_no_external,
    )
    print(json.dumps({"status": receipt["status"], "receipt": args.out, "sha256": digest}, indent=2))


if __name__ == "__main__":
    main()
