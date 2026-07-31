from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from common import (
    ALLOWED_TOOLS,
    BENCHMARK_REVISION,
    EXPERIMENT_ID,
    FROZEN_HASHES,
    MAX_OUTPUT_TOKENS,
    MAX_TOOL_CALLS,
    MAX_WALL_SECONDS,
    PINNED_MODEL,
    PREFLIGHT_PASS_SHA256,
    REASONING_EFFORT,
    SHELL_TIMEOUT_SECONDS,
    load_json,
    pretty_json_bytes,
    sha256_file,
)

ROOT = Path(__file__).resolve().parent
FROZEN = ROOT / "frozen"


def blocked(stage: str, reason: str, out: Path, detail=None):
    obj = {
        "schema": "openline.paired-mechanism-benchmark.execution-blocked.v1",
        "experiment_id": EXPERIMENT_ID,
        "benchmark_revision": BENCHMARK_REVISION,
        "status": "EXECUTION_BLOCKED",
        "failed_stage": stage,
        "failure_reason": reason,
        "benchmark_model_calls": 0,
        "real_condition_assignments": 0,
        "unblinded": False,
    }
    if detail is not None:
        obj["detail"] = detail
    blocked_out = out.with_name("EXECUTION_BLOCKED.json")
    blocked_out.parent.mkdir(parents=True, exist_ok=True)
    blocked_out.write_bytes(pretty_json_bytes(obj))
    print(json.dumps({"status": "EXECUTION_BLOCKED", "stage": stage, "receipt": str(blocked_out)}))
    raise SystemExit(1)


def verify_network_sandbox(out: Path):
    probe = (
        "python3 - <<'PY'\n"
        "import socket,sys\n"
        "try:\n"
        " socket.create_connection(('github.com',443),timeout=2)\n"
        "except OSError:\n"
        " sys.exit(0)\n"
        "sys.exit(9)\n"
        "PY"
    )
    p = subprocess.run(
        ["sudo", "unshare", "--net", "--pid", "--fork", "--mount-proc", "setpriv", f"--reuid={os.getuid()}", f"--regid={os.getgid()}", "--clear-groups", "bash", "-lc", probe],
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    if p.returncode != 0:
        blocked("network_sandbox", "agent run_shell network isolation unavailable", out, {"returncode": p.returncode})


def run_gate(*, out: Path, require_api_key: bool = True, runner_manifest: Path | None = None):
    rows = []
    for name, expected in FROZEN_HASHES.items():
        p = FROZEN / name
        if not p.exists():
            blocked("frozen_hashes", f"missing {name}", out)
        actual = sha256_file(p)
        rows.append({"file": name, "expected": expected, "actual": actual})
        if actual != expected:
            blocked("frozen_hashes", f"hash mismatch: {name}", out, rows)

    sidecar = FROZEN / "PREFLIGHT_PASS.json.sha256"
    if not sidecar.exists() or PREFLIGHT_PASS_SHA256 not in sidecar.read_text("utf-8"):
        blocked("preflight_pass", "missing or mismatched PREFLIGHT_PASS sidecar", out)
    pre = load_json(FROZEN / "PREFLIGHT_PASS.json")
    required_pre = {
        "status": "PREFLIGHT_PASS",
        "disposition": "CLEARED_FOR_SEPARATE_RANDOMIZATION_STEP",
        "git_parent_checkout_summary": "30/30 PASS",
        "benchmark_model_calls_made": 0,
        "real_condition_assignment_created": False,
    }
    for k, v in required_pre.items():
        if pre.get(k) != v:
            blocked("preflight_pass", f"PREFLIGHT_PASS semantic mismatch: {k}", out, {"expected": v, "actual": pre.get(k)})

    pair = load_json(FROZEN / "PAIR_SET_FROZEN.json")
    if pair.get("experiment_id") != EXPERIMENT_ID or len(pair.get("pairs", [])) != 30:
        blocked("pair_set", "exact 30 pair records not present", out)
    if [p.get("pair_id") for p in pair["pairs"]] != [f"P{i:02d}" for i in range(1,31)]:
        blocked("pair_set", "pair IDs are not exact P01..P30", out)

    cfg = pair.get("common_execution_config", {})
    expected_cfg = {
        "model": PINNED_MODEL,
        "reasoning_effort": REASONING_EFFORT,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "max_tool_calls": MAX_TOOL_CALLS,
        "max_wall_seconds": MAX_WALL_SECONDS,
        "shell_timeout_seconds": SHELL_TIMEOUT_SECONDS,
        "external_memory": "disabled",
        "network": "disabled",
    }
    for k, v in expected_cfg.items():
        if cfg.get(k) != v:
            blocked("execution_config", f"wrong {k}", out, {"expected": v, "actual": cfg.get(k)})
    if set((cfg.get("tools") or {}).keys()) != set(ALLOWED_TOOLS):
        blocked("execution_config", "tool allowlist mismatch", out)
    if require_api_key and not os.environ.get("OPENAI_API_KEY"):
        blocked("api_key", "OPENAI_API_KEY missing", out)

    required_bins = ["git", "python3", "bash", "sudo", "unshare", "setpriv"]
    missing = [b for b in required_bins if shutil.which(b) is None]
    if missing:
        blocked("local_tools", "required runner binaries missing", out, missing)
    verify_network_sandbox(out)

    if runner_manifest is not None:
        if not runner_manifest.exists():
            blocked("runner_manifest", "runner manifest missing", out)
        manifest = load_json(runner_manifest)
        base = runner_manifest.parent
        for rel, expected in manifest.get("files", {}).items():
            p = base / rel
            if not p.exists() or sha256_file(p) != expected:
                blocked("runner_manifest", f"runner file mismatch: {rel}", out)

    return {
        "schema": "openline.paired-mechanism-benchmark.execution-pre-run.v1",
        "experiment_id": EXPERIMENT_ID,
        "benchmark_revision": BENCHMARK_REVISION,
        "status": "PRE_RUN_GATE_PASS",
        "frozen_hashes": rows,
        "preflight_pass_sha256": PREFLIGHT_PASS_SHA256,
        "pair_count": 30,
        "model": PINNED_MODEL,
        "reasoning_effort": REASONING_EFFORT,
        "max_tool_calls": MAX_TOOL_CALLS,
        "max_wall_seconds": MAX_WALL_SECONDS,
        "shell_timeout_seconds": SHELL_TIMEOUT_SECONDS,
        "tools": list(ALLOWED_TOOLS),
        "external_memory": "disabled",
        "agent_tool_network": "denied",
        "benchmark_model_calls": 0,
        "real_condition_assignments": 0,
        "unblinded": False,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--runner-manifest")
    ap.add_argument("--allow-missing-api-key-for-test", action="store_true")
    args = ap.parse_args()
    obj = run_gate(
        out=Path(args.out),
        require_api_key=not args.allow_missing_api_key_for_test,
        runner_manifest=Path(args.runner_manifest) if args.runner_manifest else None,
    )
    Path(args.out).write_bytes(pretty_json_bytes(obj))
    print(json.dumps({"status": obj["status"], "receipt": args.out}, indent=2))


if __name__ == "__main__":
    main()
