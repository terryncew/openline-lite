from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

from assignment import decrypt_map_in_memory
from common import (
    BENCHMARK_REVISION,
    EXPERIMENT_ID,
    MAX_COMMON_PREFIX_TOOL_CALLS,
    MAX_TOOL_CALLS,
    MAX_WALL_SECONDS,
    MIN_ELIGIBLE_READ_CODEPOINTS,
    PINNED_MODEL,
    REASONING_EFFORT,
    load_json,
    pretty_json_bytes,
    sha256_bytes,
    sha256_file,
)
from perturbation import OneShotEligibleReadDelivery
from responses_agent import (
    ResponsesAPIError,
    ResponsesClient,
    append_response_output,
    function_calls,
    function_output_item,
    initial_history,
)
from tool_runtime import ToolRuntime, ToolResult
from trace_format import OperationalMapper, assert_export_safe, workspace_bytes_digest

ROOT = Path(__file__).resolve().parent
FROZEN = ROOT / "frozen"


class PairInfrastructureFailure(RuntimeError):
    pass


def run(cmd, *, cwd=None, timeout=300, input=None, env=None):
    return subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=timeout,
        input=input,
        env=env,
        check=False,
    )


def preflight_parent_map() -> dict[str, str]:
    pre = load_json(FROZEN / "PREFLIGHT_PASS.json")
    return {r["pair_id"]: r["resolved_parent_sha"] for r in pre["git_parent_checkouts"]}


def prepare_workspace(pair: dict, temp_root: Path) -> Path:
    """Checkout the exact preflight-resolved <task_commit_sha>^1 without fetching the child commit."""
    workspace = temp_root / "common"
    workspace.mkdir()
    expected_parent = preflight_parent_map().get(pair["pair_id"])
    if not expected_parent or len(expected_parent) != 40:
        raise PairInfrastructureFailure("CHECKPOINT_CANNOT_RESOLVE")
    if pair.get("checkpoint_ref") != pair.get("task_commit_sha", "") + "^1":
        raise PairInfrastructureFailure("CHECKPOINT_CANNOT_RESOLVE")

    if run(["git", "init", "-q"], cwd=workspace).returncode != 0:
        raise PairInfrastructureFailure("CHECKPOINT_CANNOT_RESOLVE")
    remote = f'https://github.com/{pair["repository"]}.git'
    if run(["git", "remote", "add", "origin", remote], cwd=workspace).returncode != 0:
        raise PairInfrastructureFailure("CHECKPOINT_CANNOT_RESOLVE")

    # Fetch only the already-verified parent SHA. The historical child/task commit and its
    # solution diff never enter the agent workspace/object database.
    f = run(["git", "fetch", "-q", "--depth=1", "--no-tags", "origin", expected_parent], cwd=workspace, timeout=300)
    if f.returncode != 0:
        raise PairInfrastructureFailure("CHECKPOINT_CANNOT_RESOLVE")
    co = run(["git", "checkout", "-q", "--detach", expected_parent], cwd=workspace, timeout=300)
    if co.returncode != 0:
        raise PairInfrastructureFailure("CHECKPOINT_CANNOT_RESOLVE")
    head = run(["git", "rev-parse", "HEAD"], cwd=workspace)
    status = run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=workspace)
    if head.returncode != 0 or head.stdout.strip() != expected_parent or status.returncode != 0 or status.stdout.strip():
        raise PairInfrastructureFailure("CHECKPOINT_CANNOT_RESOLVE")
    return workspace


def clone_workspace(common: Path, dest: Path):
    shutil.copytree(common, dest, symlinks=True)


def safe_args(call: dict) -> dict:
    try:
        obj = json.loads(call.get("arguments") or "{}")
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def make_invalid_record(opaque_id: str, pair_id: str, reason: str, *, model_calls: int, returned_models: set[str]) -> dict:
    obj = {
        "schema": "openline.paired-mechanism-benchmark.invalid-execution.v1",
        "experiment_id": EXPERIMENT_ID,
        "benchmark_revision": BENCHMARK_REVISION,
        "opaque_execution_id": opaque_id,
        "pair_id": pair_id,
        "disposition": "PAIR_INSTRUMENTATION_INVALID",
        "invalidity_reason": reason,
        "benchmark_model_calls_observed": model_calls,
        "requested_model": PINNED_MODEL,
        "returned_models": sorted(returned_models),
        "reasoning_effort": REASONING_EFFORT,
        "unblinded": False,
    }
    assert_export_safe(obj)
    return obj


def make_trace(opaque_id: str, pair_id: str, steps: list, termination: dict, *, model_calls: int, returned_models: set[str]) -> dict:
    obj = {
        "schema": "openline.paired-mechanism-benchmark.opaque-trace.v1",
        "experiment_id": EXPERIMENT_ID,
        "benchmark_revision": BENCHMARK_REVISION,
        "opaque_execution_id": opaque_id,
        "pair_id": pair_id,
        "disposition": "TRACE_VALID",
        "scoring_anchor": "immediately_before_eligible_read_result_delivery",
        "steps": steps,
        "step_count": len(steps),
        "termination": termination,
        "benchmark_model_calls_observed": model_calls,
        "requested_model": PINNED_MODEL,
        "returned_models": sorted(returned_models),
        "reasoning_effort": REASONING_EFFORT,
        "raw_tool_payloads_present": False,
        "unblinded": False,
    }
    assert_export_safe(obj)
    return obj


def write_pair_outputs(out_dir: Path, rows: dict[str, dict], pair_receipt: dict):
    out_dir.mkdir(parents=True, exist_ok=True)
    for opaque, obj in rows.items():
        path = out_dir / f"{opaque}.json"
        path.write_bytes(pretty_json_bytes(obj))
        (out_dir / f"{opaque}.json.sha256").write_text(f"{sha256_file(path)}  {opaque}.json\n", encoding="utf-8")
    pair_path = out_dir / f'{pair_receipt["pair_id"]}.verification.json'
    pair_path.write_bytes(pretty_json_bytes(pair_receipt))


def execute_common_until_fork(client: ResponsesClient, pair: dict, workspace: Path):
    history = initial_history(pair["task_prompt"])
    runtime = ToolRuntime(workspace)
    tool_count = 0
    common_start = time.monotonic()
    while True:
        elapsed = time.monotonic() - common_start
        if elapsed >= MAX_WALL_SECONDS:
            return {"status": "INVALID", "reason": "NO_ELIGIBLE_READ_WITHIN_FIRST_20_COMMON_PREFIX_TOOL_CALLS", "tool_count": tool_count, "elapsed": elapsed}
        try:
            resp = client.create(instructions=pair["system_prompt"], history=history, timeout=max(1, int(MAX_WALL_SECONDS - elapsed)))
        except ResponsesAPIError as e:
            raise PairInfrastructureFailure("MODEL_API_FAILURE") from e
        history = append_response_output(history, resp)
        calls = function_calls(resp)
        if not calls:
            return {"status": "INVALID", "reason": "NO_ELIGIBLE_READ_WITHIN_FIRST_20_COMMON_PREFIX_TOOL_CALLS", "tool_count": tool_count, "elapsed": time.monotonic() - common_start}
        for idx, call in enumerate(calls):
            tool_count += 1
            if tool_count > MAX_COMMON_PREFIX_TOOL_CALLS or tool_count > MAX_TOOL_CALLS:
                return {"status": "INVALID", "reason": "NO_ELIGIBLE_READ_WITHIN_FIRST_20_COMMON_PREFIX_TOOL_CALLS", "tool_count": tool_count - 1, "elapsed": time.monotonic() - common_start}
            name = call.get("name")
            args = safe_args(call)
            result = runtime.execute(name, args)
            if name == "read_file" and result.read_text is not None and result.read_relpath is not None and len(result.read_text) >= MIN_ELIGIBLE_READ_CODEPOINTS:
                # Fork occurs before this result is delivered. Preserve later already-requested calls for branch execution.
                return {
                    "status": "FORK",
                    "history": history,
                    "tool_count": tool_count,
                    "elapsed": time.monotonic() - common_start,
                    "eligible_call": call,
                    "eligible_result": result,
                    "remaining_calls": calls[idx + 1 :],
                }
            history.append(function_output_item(call.get("call_id"), result.output))


def execute_branch(
    *,
    client: ResponsesClient,
    pair: dict,
    workspace: Path,
    opaque_id: str,
    history_at_fork: list,
    common_tool_count: int,
    common_elapsed: float,
    eligible_call: dict,
    eligible_result: ToolResult,
    remaining_calls: list[dict],
    alter_eligible_result: bool,
):
    history = copy.deepcopy(history_at_fork)
    runtime = ToolRuntime(workspace)
    mapper = OperationalMapper(workspace)
    steps = []
    tool_count = common_tool_count
    branch_start = time.monotonic()
    active_budget = max(0.0, MAX_WALL_SECONDS - common_elapsed)

    delivery = OneShotEligibleReadDelivery()
    delivered = delivery.deliver(eligible_result.read_text, alter=alter_eligible_result)
    history.append(function_output_item(eligible_call.get("call_id"), delivered))
    steps.append(mapper.record_completed_tool(eligible_result.observation))

    # Execute any later calls that were already requested in the same model response, identically by opaque branch.
    for call in remaining_calls:
        if tool_count >= MAX_TOOL_CALLS:
            break
        tool_count += 1
        result = runtime.execute(call.get("name"), safe_args(call))
        history.append(function_output_item(call.get("call_id"), result.output))
        steps.append(mapper.record_completed_tool(result.observation))

    termination = None
    while tool_count < MAX_TOOL_CALLS:
        elapsed_branch = time.monotonic() - branch_start
        if elapsed_branch >= active_budget:
            termination = {"kind": "MAX_WALL_SECONDS", "tool_calls_total": tool_count, "active_seconds": common_elapsed + elapsed_branch}
            break
        try:
            resp = client.create(
                instructions=pair["system_prompt"],
                history=history,
                timeout=max(1, int(active_budget - elapsed_branch)),
            )
        except ResponsesAPIError as e:
            raise PairInfrastructureFailure("MODEL_API_FAILURE") from e
        history = append_response_output(history, resp)
        calls = function_calls(resp)
        if not calls:
            termination = {"kind": "FINAL_AGENT_ANSWER", "tool_calls_total": tool_count, "active_seconds": common_elapsed + (time.monotonic() - branch_start)}
            break
        for call in calls:
            if tool_count >= MAX_TOOL_CALLS:
                break
            tool_count += 1
            result = runtime.execute(call.get("name"), safe_args(call))
            history.append(function_output_item(call.get("call_id"), result.output))
            steps.append(mapper.record_completed_tool(result.observation))
        if tool_count >= MAX_TOOL_CALLS:
            termination = {"kind": "MAX_TOOL_CALLS", "tool_calls_total": tool_count, "active_seconds": common_elapsed + (time.monotonic() - branch_start)}
            break
    if termination is None:
        termination = {"kind": "MAX_TOOL_CALLS", "tool_calls_total": tool_count, "active_seconds": common_elapsed + (time.monotonic() - branch_start)}
    return steps, termination


def run_pair(*, pair_id: str, manifest_path: Path, sealed_zip: Path, key_path: Path, out_dir: Path):
    pair_set = load_json(FROZEN / "PAIR_SET_FROZEN.json")
    cfg = pair_set["common_execution_config"]
    pair = next((p for p in pair_set["pairs"] if p["pair_id"] == pair_id), None)
    if pair is None:
        raise ValueError("unknown pair_id")
    pair = {**pair, "system_prompt": cfg["system_prompt"]}

    manifest = load_json(manifest_path)
    public_rows = [r for r in manifest["executions"] if r["pair_id"] == pair_id]
    if len(public_rows) != 2:
        raise ValueError("pair manifest must contain exactly two opaque executions")
    opaque_ids = [r["opaque_execution_id"] for r in sorted(public_rows, key=lambda r: r["execution_order"])]

    private = Path(tempfile.mkdtemp(prefix=f"olp-private-{pair_id}-"))
    try:
        with zipfile.ZipFile(sealed_zip) as z:
            z.extractall(private / "sealed")
        secret_map = decrypt_map_in_memory(private / "sealed", key_path)
        conditions = {r["opaque_execution_id"]: r["condition"] for r in secret_map["conditions"] if r["pair_id"] == pair_id}
        if set(conditions) != set(opaque_ids) or sorted(conditions.values()) != ["CLEAN", "PERTURBED"]:
            raise PairInfrastructureFailure("ASSIGNMENT_INTEGRITY_FAILURE")
        # Remove all assignment/key material from the filesystem before any model/tool execution.
        try:
            key_path.unlink()
        except FileNotFoundError:
            pass
        try:
            sealed_zip.unlink()
        except FileNotFoundError:
            pass
        shutil.rmtree(private / "sealed", ignore_errors=True)

        temp_root = Path(tempfile.mkdtemp(prefix=f"olp-pair-{pair_id}-"))
        try:
            client = ResponsesClient(os.environ.get("OPENAI_API_KEY", ""))
            rows = {}
            try:
                common = prepare_workspace(pair, temp_root)
            except PairInfrastructureFailure:
                for oid in opaque_ids:
                    rows[oid] = make_invalid_record(oid, pair_id, "CHECKPOINT_CANNOT_RESOLVE", model_calls=0, returned_models=set())
                receipt = {
                    "schema": "openline.paired-mechanism-benchmark.pair-verification.v1",
                    "experiment_id": EXPERIMENT_ID,
                    "pair_id": pair_id,
                    "opaque_execution_ids": sorted(opaque_ids),
                    "pair_disposition": "PAIR_INSTRUMENTATION_INVALID",
                    "invalidity_reason": "CHECKPOINT_CANNOT_RESOLVE",
                    "benchmark_model_calls": 0,
                    "returned_models": [],
                    "unblinded": False,
                }
                write_pair_outputs(out_dir, rows, receipt)
                return receipt

            try:
                fork = execute_common_until_fork(client, pair, common)
                if fork["status"] != "FORK":
                    reason = "NO_ELIGIBLE_READ_WITHIN_FIRST_20_COMMON_PREFIX_TOOL_CALLS"
                    for oid in opaque_ids:
                        rows[oid] = make_invalid_record(oid, pair_id, reason, model_calls=client.call_count, returned_models=client.returned_models)
                    receipt = {
                        "schema": "openline.paired-mechanism-benchmark.pair-verification.v1",
                        "experiment_id": EXPERIMENT_ID,
                        "pair_id": pair_id,
                        "opaque_execution_ids": sorted(opaque_ids),
                        "pair_disposition": "PAIR_INSTRUMENTATION_INVALID",
                        "invalidity_reason": reason,
                        "benchmark_model_calls": client.call_count,
                        "returned_models": sorted(client.returned_models),
                        "unblinded": False,
                    }
                    write_pair_outputs(out_dir, rows, receipt)
                    return receipt

                xdir, ydir = temp_root / "X", temp_root / "Y"
                clone_workspace(common, xdir)
                clone_workspace(common, ydir)
                dx, dy = workspace_bytes_digest(xdir), workspace_bytes_digest(ydir)
                if dx != dy:
                    reason = "WORKSPACE_CANNOT_FORK_BYTE_IDENTICALLY"
                    for oid in opaque_ids:
                        rows[oid] = make_invalid_record(oid, pair_id, reason, model_calls=client.call_count, returned_models=client.returned_models)
                    receipt = {
                        "schema": "openline.paired-mechanism-benchmark.pair-verification.v1",
                        "experiment_id": EXPERIMENT_ID,
                        "pair_id": pair_id,
                        "opaque_execution_ids": sorted(opaque_ids),
                        "pair_disposition": "PAIR_INSTRUMENTATION_INVALID",
                        "invalidity_reason": reason,
                        "benchmark_model_calls": client.call_count,
                        "returned_models": sorted(client.returned_models),
                        "unblinded": False,
                    }
                    write_pair_outputs(out_dir, rows, receipt)
                    return receipt

                # Branch execution order is the blinded manifest order. The boolean is kept private in-process only.
                branch_results = {}
                for oid in opaque_ids:
                    suffix = oid.rsplit("-", 1)[1]
                    ws = xdir if suffix == "X" else ydir
                    alter = conditions[oid] == "PERTURBED"
                    steps, termination = execute_branch(
                        client=client,
                        pair=pair,
                        workspace=ws,
                        opaque_id=oid,
                        history_at_fork=fork["history"],
                        common_tool_count=fork["tool_count"],
                        common_elapsed=fork["elapsed"],
                        eligible_call=fork["eligible_call"],
                        eligible_result=fork["eligible_result"],
                        remaining_calls=fork["remaining_calls"],
                        alter_eligible_result=alter,
                    )
                    branch_results[oid] = (steps, termination)

                # Pair invalidity is decided without using assignment labels.
                pair_invalid = any(len(steps) < 3 for steps, _ in branch_results.values())
                if pair_invalid:
                    reason = "REQUIRED_FROZEN_SIGNAL_OBSERVATIONS_CANNOT_BE_EMITTED"
                    for oid in opaque_ids:
                        rows[oid] = make_invalid_record(oid, pair_id, reason, model_calls=client.call_count, returned_models=client.returned_models)
                    disposition = "PAIR_INSTRUMENTATION_INVALID"
                else:
                    for oid, (steps, term) in branch_results.items():
                        rows[oid] = make_trace(oid, pair_id, steps, term, model_calls=client.call_count, returned_models=client.returned_models)
                    disposition = "PAIR_VALID_FOR_BLIND_SCORING"

                receipt = {
                    "schema": "openline.paired-mechanism-benchmark.pair-verification.v1",
                    "experiment_id": EXPERIMENT_ID,
                    "benchmark_revision": BENCHMARK_REVISION,
                    "pair_id": pair_id,
                    "opaque_execution_ids": sorted(opaque_ids),
                    "pair_disposition": disposition,
                    "benchmark_model_calls": client.call_count,
                    "returned_models": sorted(client.returned_models),
                    "reasoning_effort": REASONING_EFFORT,
                    "fork_workspace_sha256": dx,
                    "branch_config_equal": True,
                    "unblinded": False,
                }
                if pair_invalid:
                    receipt["invalidity_reason"] = "REQUIRED_FROZEN_SIGNAL_OBSERVATIONS_CANNOT_BE_EMITTED"
                assert_export_safe(receipt)
                write_pair_outputs(out_dir, rows, receipt)
                return receipt
            except PairInfrastructureFailure:
                # Infrastructure failure is NOT converted into a new scientific invalidity rule.
                raise
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)
    finally:
        shutil.rmtree(private, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair-id", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--sealed-condition-zip", required=True)
    ap.add_argument("--secret-key", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    try:
        receipt = run_pair(
            pair_id=args.pair_id,
            manifest_path=Path(args.manifest),
            sealed_zip=Path(args.sealed_condition_zip),
            key_path=Path(args.secret_key),
            out_dir=Path(args.out_dir),
        )
        print(json.dumps({"pair_id": args.pair_id, "status": receipt["pair_disposition"]}, indent=2))
    except PairInfrastructureFailure as e:
        # Infrastructure failure is evidence, not a new scientific invalidity rule.
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        rec = {
            "schema": "openline.paired-mechanism-benchmark.infrastructure-failure.v1",
            "experiment_id": EXPERIMENT_ID,
            "benchmark_revision": BENCHMARK_REVISION,
            "pair_id": args.pair_id,
            "status": "EXECUTION_INFRASTRUCTURE_FAILURE",
            "reason": str(e),
            "unblinded": False,
        }
        (out / f"{args.pair_id}.infrastructure.json").write_bytes(pretty_json_bytes(rec))
        print(json.dumps({"pair_id": args.pair_id, "status": "EXECUTION_INFRASTRUCTURE_FAILURE", "reason": str(e)}, indent=2))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
