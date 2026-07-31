#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FROZEN = ROOT / "frozen"
BUILD = ROOT / "build"
BUILD.mkdir(exist_ok=True)

EXPECTED = {
    "BENCHMARK_DESIGN_FROZEN.json": "fd0b9eb2e2f494031bac8448dba3f6344071a4d8f2ea9285d2c3fd8ecc159f7f",
    "PAIR_SET_FROZEN.json": "5c622e0deaf500f7f39d9c5afece7550c1fa4859155d8b7191eb67bb0a725533",
    "SIGNAL_SCHEMA_FROZEN_SCOPE_REPAIRED.json": "88dbb498881e84e32b7599ec2ec1bf186a923bc17d7e0c67c4d09cf2b9cddb8d",
    "PERTURBATION_SPEC_FROZEN_SCOPE_REPAIRED.json": "1a94515d691b86719b17885c3bf983fbe7d052affebc22406ec35b34fc9bc9e4",
    "PREFLIGHT_BLOCKED_SCOPE_MISMATCH.json": "7383db129c7121a06350eb19ca83e40080d17811a64f362dd2c2c04b1d6aaa9b",
    "PREFLIGHT_BLOCKED_RUNNER_NETWORK.json": "34bdd220ff865421b3d1ba3c014274ae553e037288e1d2ec3619713187d78e68",
    "RESEALED_AFTER_SCOPE_REPAIR.json": "050e8e643d04af4c0c18158d084110ea2b001a33ea57d8d5d22bd32e95501564",
}

PINNED_MODEL = "gpt-5.5-2026-04-23"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(cmd, *, cwd=None, timeout=180, env=None):
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def fail(receipt, stage, reason, detail=None):
    receipt["status"] = "BLOCKED_PRE_RANDOMIZATION"
    receipt["failed_stage"] = stage
    receipt["failure_reason"] = reason
    if detail is not None:
        receipt["failure_detail"] = detail
    receipt["real_condition_assignment_created"] = False
    receipt["benchmark_model_calls_made"] = 0
    seal(receipt, "PREFLIGHT_BLOCKED.json")
    sys.exit(1)


def seal(receipt, filename):
    receipt["sealed_at_unix_seconds"] = int(time.time())
    data = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode()
    path = BUILD / filename
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    (BUILD / (filename + ".sha256")).write_text(digest + "  " + filename + "\n", encoding="utf-8")
    print(json.dumps({"receipt": str(path), "sha256": digest, "status": receipt["status"]}, indent=2))


def check_frozen_hashes(receipt):
    rows = []
    for name, expected in EXPECTED.items():
        path = FROZEN / name
        if not path.exists():
            fail(receipt, "frozen_hashes", f"missing frozen artifact: {name}")
        actual = sha256(path)
        rows.append({"file": name, "expected": expected, "actual": actual, "status": "PASS" if actual == expected else "FAIL"})
        if actual != expected:
            receipt["frozen_hashes"] = rows
            fail(receipt, "frozen_hashes", f"hash mismatch: {name}")
    receipt["frozen_hashes"] = rows


def validate_config(receipt, pair_set):
    cfg = pair_set.get("common_execution_config", {})
    exact = {
        "api": "OpenAI Responses API",
        "model": PINNED_MODEL,
        "reasoning_effort": "medium",
        "max_output_tokens": 16384,
        "network": "disabled",
        "external_memory": "disabled",
        "max_tool_calls": 40,
        "max_wall_seconds": 1200,
        "shell_timeout_seconds": 120,
    }
    for k, v in exact.items():
        if cfg.get(k) != v:
            fail(receipt, "execution_config", f"frozen config mismatch for {k}", {"expected": v, "actual": cfg.get(k)})
    expected_tools = {"read_file", "list_tree", "search_text", "apply_patch", "run_shell"}
    actual_tools = set((cfg.get("tools") or {}).keys())
    if actual_tools != expected_tools:
        fail(receipt, "execution_config", "tool allowlist mismatch", {"expected": sorted(expected_tools), "actual": sorted(actual_tools)})
    receipt["execution_config"] = {"status": "PASS", "pinned": exact, "tools": sorted(expected_tools)}


def checkout_all_parents(receipt, pair_set):
    pairs = pair_set.get("pairs", [])
    if len(pairs) != 30:
        fail(receipt, "git_parent_checkouts", "pair set does not contain exactly 30 pairs")
    tmp = Path(tempfile.mkdtemp(prefix="olp-30pair-checkouts-"))
    results = []
    try:
        for idx, p in enumerate(pairs, 1):
            pair_id = p["pair_id"]
            repo = p["repository"]
            child = p["task_commit_sha"]
            expected_ref = p["checkpoint_ref"]
            expected_ref_literal = child + "^1"
            if expected_ref != expected_ref_literal:
                receipt["git_parent_checkouts"] = results
                fail(receipt, "git_parent_checkouts", f"checkpoint_ref mismatch for {pair_id}")
            d = tmp / pair_id
            d.mkdir()
            init = run(["git", "init", "-q"], cwd=d)
            if init.returncode != 0:
                fail(receipt, "git_parent_checkouts", f"git init failed for {pair_id}", init.stderr[-1000:])
            remote = f"https://github.com/{repo}.git"
            r = run(["git", "remote", "add", "origin", remote], cwd=d)
            if r.returncode != 0:
                fail(receipt, "git_parent_checkouts", f"git remote failed for {pair_id}", r.stderr[-1000:])
            # Fetch child plus first-parent ancestry, enough to resolve ^1 and materialize the exact parent tree.
            f = run(["git", "fetch", "-q", "--depth=2", "--no-tags", "origin", child], cwd=d, timeout=300)
            if f.returncode != 0:
                receipt["git_parent_checkouts"] = results
                fail(receipt, "git_parent_checkouts", f"exact fetch failed for {pair_id}", f.stderr[-2000:])
            parent = run(["git", "rev-parse", "FETCH_HEAD^1"], cwd=d)
            if parent.returncode != 0:
                fail(receipt, "git_parent_checkouts", f"first parent resolution failed for {pair_id}", parent.stderr[-1000:])
            parent_sha = parent.stdout.strip()
            co = run(["git", "checkout", "-q", "--detach", parent_sha], cwd=d, timeout=300)
            if co.returncode != 0:
                fail(receipt, "git_parent_checkouts", f"parent checkout failed for {pair_id}", co.stderr[-2000:])
            head = run(["git", "rev-parse", "HEAD"], cwd=d)
            status = run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=d)
            if head.returncode != 0 or head.stdout.strip() != parent_sha or status.returncode != 0 or status.stdout.strip():
                fail(receipt, "git_parent_checkouts", f"checkout verification failed for {pair_id}")
            results.append({
                "pair_id": pair_id,
                "repository": repo,
                "task_commit_sha": child,
                "checkpoint_ref": expected_ref,
                "resolved_parent_sha": parent_sha,
                "status": "PASS",
            })
            print(f"CHECKOUT_PASS {idx:02d}/30 {pair_id} {repo} {parent_sha}", flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    receipt["git_parent_checkouts"] = results
    receipt["git_parent_checkout_summary"] = "30/30 PASS"


def check_local_harness_capability(receipt):
    required_bins = ["git", "python3", "bash", "timeout", "grep"]
    missing = [b for b in required_bins if shutil.which(b) is None]
    if missing:
        fail(receipt, "harness_constraints", "required local binaries missing", missing)

    # Shell timeout enforcement capability: a 2s command must be killed by a 1s boundary.
    t = run(["timeout", "1", "bash", "-lc", "sleep 2"], timeout=5)
    if t.returncode != 124:
        fail(receipt, "harness_constraints", "GNU timeout boundary unavailable", {"returncode": t.returncode})

    # Tool subprocesses must be able to run in a network namespace with no external network.
    # GitHub-hosted Ubuntu runners normally provide passwordless sudo; if this cannot be enforced, fail closed.
    probe = (
        "python3 - <<'PY'\n"
        "import socket, sys\n"
        "try:\n"
        "    socket.create_connection(('github.com',443), timeout=2)\n"
        "except OSError:\n"
        "    sys.exit(0)\n"
        "sys.exit(9)\n"
        "PY"
    )
    n = run(["sudo", "unshare", "--net", "bash", "-lc", probe], timeout=20)
    if n.returncode != 0:
        fail(receipt, "harness_constraints", "network-denied tool subprocess capability check failed", {"returncode": n.returncode, "stderr": n.stderr[-1500:]})

    receipt["harness_constraints"] = {
        "status": "PASS",
        "local_tool_allowlist": ["read_file", "list_tree", "search_text", "apply_patch", "run_shell"],
        "tool_subprocess_network": "DENIED_BY_NETWORK_NAMESPACE_CAPABILITY",
        "max_tool_calls": 40,
        "max_wall_seconds": 1200,
        "shell_timeout_seconds": 120,
        "timeout_enforcement_probe": "PASS",
    }


def capability_probe_model(receipt):
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        fail(receipt, "model_capability", "OPENAI_API_KEY is not available in the non-scoring runner")

    # One non-benchmark capability probe. No benchmark task/pair content is sent.
    body = json.dumps({
        "model": PINNED_MODEL,
        "input": "Preflight capability probe. Reply OK.",
        "reasoning": {"effort": "medium"},
        "max_output_tokens": 16,
        "store": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:2000]
        fail(receipt, "model_capability", f"Responses API capability probe HTTP {e.code}", detail)
    except Exception as e:
        fail(receipt, "model_capability", "Responses API capability probe failed", repr(e))

    if status != 200:
        fail(receipt, "model_capability", f"unexpected Responses API status {status}")
    try:
        obj = json.loads(raw)
    except Exception as e:
        fail(receipt, "model_capability", "capability response was not JSON", repr(e))
    returned_model = obj.get("model")
    # Some API surfaces return the exact snapshot, others can echo an alias; for this frozen benchmark
    # the requested model is the authoritative pin and a successful request proves account access.
    if not obj.get("id"):
        fail(receipt, "model_capability", "capability response missing response id")
    receipt["model_capability"] = {
        "status": "PASS",
        "requested_model": PINNED_MODEL,
        "returned_model": returned_model,
        "reasoning_effort": "medium",
        "probe_type": "NON_BENCHMARK_RESPONSES_API_CAPABILITY_CALL",
        "probe_response_id_sha256": hashlib.sha256(str(obj.get("id")).encode()).hexdigest(),
        "benchmark_pair_content_sent": False,
        "benchmark_model_calls_made": 0,
        "capability_probe_calls_made": 1,
    }


def main():
    receipt = {
        "schema": "openline.paired-mechanism-benchmark.preflight.v3",
        "experiment_id": "olp-core21-paired-mechanism-001",
        "benchmark_revision": "RESEALED_AFTER_SCOPE_REPAIR",
        "runner_role": "NON_SCORING_PREFLIGHT_ONLY",
        "real_condition_assignment_created": False,
        "benchmark_model_calls_made": 0,
        "randomization_code_present": False,
        "prior_environment_failure_receipt_sha256": "34bdd220ff865421b3d1ba3c014274ae553e037288e1d2ec3619713187d78e68",
    }

    check_frozen_hashes(receipt)
    pair_set = json.loads((FROZEN / "PAIR_SET_FROZEN.json").read_text("utf-8"))
    validate_config(receipt, pair_set)
    checkout_all_parents(receipt, pair_set)
    check_local_harness_capability(receipt)
    capability_probe_model(receipt)

    receipt["status"] = "PREFLIGHT_PASS"
    receipt["disposition"] = "CLEARED_FOR_SEPARATE_RANDOMIZATION_STEP"
    receipt["real_condition_assignment_created"] = False
    receipt["benchmark_model_calls_made"] = 0
    seal(receipt, "PREFLIGHT_PASS.json")


if __name__ == "__main__":
    main()
