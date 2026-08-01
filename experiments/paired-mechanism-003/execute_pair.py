from __future__ import annotations

import argparse
import copy
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
    CHECKPOINT_CHECKOUT_TIMEOUT_SECONDS,
    CHECKPOINT_FETCH_TIMEOUT_SECONDS,
    EXPERIMENT_ID,
    MAX_COMMON_PREFIX_TOOL_CALLS,
    MAX_TOOL_CALLS,
    MAX_WALL_SECONDS,
    MIN_ELIGIBLE_READ_CODEPOINTS,
    PINNED_MODEL,
    REASONING_EFFORT,
    load_json,
    pretty_json_bytes,
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
FROZEN = ROOT / "frozen_scientific"


class PairInfrastructureFailure(RuntimeError):
    def __init__(self, reason: str, *, detail: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail or {}


def run(cmd, *, cwd=None, timeout=300, input=None, env=None):
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, timeout=timeout, input=input, env=env, check=False)


def parent_map() -> dict[str, dict]:
    return load_json(ROOT / "PARENT_MAP_FROZEN_003.json")["pairs"]


def prepare_workspace(pair: dict, temp_root: Path) -> Path:
    """Checkout only the exact pre-frozen parent SHA; never fetch the historical child solution commit."""
    workspace = temp_root / "common"
    workspace.mkdir()
    row = parent_map().get(pair["pair_id"])
    expected_parent = (row or {}).get("resolved_parent_sha")
    if not expected_parent or len(expected_parent) != 40:
        raise PairInfrastructureFailure("CHECKPOINT_CANNOT_RESOLVE")
    if pair.get("checkpoint_ref") != pair.get("task_commit_sha", "") + "^1":
        raise PairInfrastructureFailure("CHECKPOINT_CANNOT_RESOLVE")
    if (row or {}).get("task_commit_sha") != pair.get("task_commit_sha") or (row or {}).get("checkpoint_ref") != pair.get("checkpoint_ref"):
        raise PairInfrastructureFailure("CHECKPOINT_BINDING_MISMATCH")
    if run(["git", "init", "-q"], cwd=workspace).returncode != 0:
        raise PairInfrastructureFailure("CHECKPOINT_CANNOT_RESOLVE")
    remote = f'https://github.com/{pair["repository"]}.git'
    if run(["git", "remote", "add", "origin", remote], cwd=workspace).returncode != 0:
        raise PairInfrastructureFailure("CHECKPOINT_CANNOT_RESOLVE")
    f = run(["git", "fetch", "-q", "--depth=1", "--no-tags", "origin", expected_parent], cwd=workspace, timeout=CHECKPOINT_FETCH_TIMEOUT_SECONDS)
    if f.returncode != 0:
        raise PairInfrastructureFailure("CHECKPOINT_FETCH_FAILURE", detail={"git_returncode": f.returncode})
    co = run(["git", "checkout", "-q", "--detach", expected_parent], cwd=workspace, timeout=CHECKPOINT_CHECKOUT_TIMEOUT_SECONDS)
    if co.returncode != 0:
        raise PairInfrastructureFailure("CHECKPOINT_CHECKOUT_FAILURE", detail={"git_returncode": co.returncode})
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


def model_failure(exc: ResponsesAPIError, client: ResponsesClient, phase: str) -> PairInfrastructureFailure:
    return PairInfrastructureFailure(
        exc.detail.category,
        detail={
            "failure_class": "MODEL_API",
            "execution_phase": phase,
            "api_failure": exc.public_dict(),
            "api_metrics": client.metrics(),
        },
    )




def public_client_metrics(client: ResponsesClient | None) -> dict:
    if client is None:
        return {
            "benchmark_model_calls": 0,
            "benchmark_completed_responses": 0,
            "benchmark_retry_count": 0,
            "benchmark_input_tokens": 0,
            "benchmark_output_tokens": 0,
            "benchmark_total_tokens": 0,
            "benchmark_cached_input_tokens": 0,
            "infrastructure_wait_seconds": 0.0,
            "active_api_seconds": 0.0,
            "request_start_events": [],
            "rate_limit_header_samples": [],
            "returned_models": [],
        }
    headers = list(client.response_rate_limit_headers)
    samples = headers if len(headers) <= 2 else [headers[0], headers[-1]]
    return {
        "benchmark_model_calls": client.api_attempt_count,
        "benchmark_completed_responses": client.completed_response_count,
        "benchmark_retry_count": client.retry_count,
        "benchmark_input_tokens": client.input_tokens,
        "benchmark_output_tokens": client.output_tokens,
        "benchmark_total_tokens": client.total_tokens,
        "benchmark_cached_input_tokens": client.cached_input_tokens,
        "infrastructure_wait_seconds": client.infrastructure_wait_seconds,
        "active_api_seconds": client.active_api_seconds,
        "request_start_events": list(client.request_start_events),
        "rate_limit_header_samples": samples,
        "returned_models": sorted(client.returned_models),
    }

def make_invalid_record(opaque_id: str, pair_id: str, reason: str, *, client: ResponsesClient | None) -> dict:
    api_attempts = 0 if client is None else client.api_attempt_count
    completed = 0 if client is None else client.completed_response_count
    retries = 0 if client is None else client.retry_count
    infra_wait = 0.0 if client is None else client.infrastructure_wait_seconds
    active_api = 0.0 if client is None else client.active_api_seconds
    returned = [] if client is None else sorted(client.returned_models)
    obj = {
        "schema": "openline.paired-mechanism-benchmark.invalid-execution.v1",
        "experiment_id": EXPERIMENT_ID,
        "benchmark_revision": BENCHMARK_REVISION,
        "opaque_execution_id": opaque_id,
        "pair_id": pair_id,
        "disposition": "PAIR_INSTRUMENTATION_INVALID",
        "invalidity_reason": reason,
        "benchmark_model_calls_observed": api_attempts,
        "benchmark_completed_responses_observed": completed,
        "benchmark_retry_count_observed": retries,
        "infrastructure_wait_seconds_observed": infra_wait,
        "active_api_seconds_observed": active_api,
        "requested_model": PINNED_MODEL,
        "returned_models": returned,
        "reasoning_effort": REASONING_EFFORT,
        "unblinded": False,
    }
    assert_export_safe(obj)
    return obj


def make_trace(opaque_id: str, pair_id: str, steps: list, termination: dict, *, client: ResponsesClient) -> dict:
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
        "benchmark_model_calls_observed": client.api_attempt_count,
        "benchmark_completed_responses_observed": client.completed_response_count,
        "benchmark_retry_count_observed": client.retry_count,
        "infrastructure_wait_seconds_observed": client.infrastructure_wait_seconds,
        "active_api_seconds_observed": client.active_api_seconds,
        "requested_model": PINNED_MODEL,
        "returned_models": sorted(client.returned_models),
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


class ActiveBudget:
    """Scientific active-time budget. Pacing and retry waits are excluded."""

    def __init__(self, total_seconds: float):
        self.total_seconds = float(total_seconds)
        self.used_seconds = 0.0

    @property
    def remaining(self) -> float:
        return max(0.0, self.total_seconds - self.used_seconds)

    def consume(self, seconds: float) -> None:
        self.used_seconds = min(self.total_seconds, self.used_seconds + max(0.0, float(seconds)))


def _model_create_active(client: ResponsesClient, budget: ActiveBudget, *, instructions: str, history: list):
    before = client.active_api_seconds
    try:
        return client.create(instructions=instructions, history=history, timeout=budget.remaining)
    finally:
        budget.consume(client.active_api_seconds - before)


def _tool_active(runtime: ToolRuntime, budget: ActiveBudget, name: str, args: dict):
    started = time.monotonic()
    try:
        return runtime.execute(name, args, max_seconds=budget.remaining)
    finally:
        budget.consume(time.monotonic() - started)


def execute_common_until_fork(client: ResponsesClient, pair: dict, workspace: Path):
    history = initial_history(pair["task_prompt"])
    runtime = ToolRuntime(workspace)
    tool_count = 0
    budget = ActiveBudget(MAX_WALL_SECONDS)
    while True:
        if budget.remaining <= 0:
            return {"status": "INVALID", "reason": "NO_ELIGIBLE_READ_WITHIN_FIRST_20_COMMON_PREFIX_TOOL_CALLS", "tool_count": tool_count, "elapsed": MAX_WALL_SECONDS}
        try:
            resp = _model_create_active(client, budget, instructions=pair["system_prompt"], history=history)
        except ResponsesAPIError as exc:
            raise model_failure(exc, client, "COMMON_PREFIX") from None
        history = append_response_output(history, resp)
        calls = function_calls(resp)
        if not calls:
            return {"status": "INVALID", "reason": "NO_ELIGIBLE_READ_WITHIN_FIRST_20_COMMON_PREFIX_TOOL_CALLS", "tool_count": tool_count, "elapsed": budget.used_seconds}
        for idx, call in enumerate(calls):
            if budget.remaining <= 0:
                return {"status": "INVALID", "reason": "NO_ELIGIBLE_READ_WITHIN_FIRST_20_COMMON_PREFIX_TOOL_CALLS", "tool_count": tool_count, "elapsed": MAX_WALL_SECONDS}
            tool_count += 1
            if tool_count > MAX_COMMON_PREFIX_TOOL_CALLS or tool_count > MAX_TOOL_CALLS:
                return {"status": "INVALID", "reason": "NO_ELIGIBLE_READ_WITHIN_FIRST_20_COMMON_PREFIX_TOOL_CALLS", "tool_count": tool_count - 1, "elapsed": budget.used_seconds}
            name = call.get("name")
            args = safe_args(call)
            result = _tool_active(runtime, budget, name, args)
            if name == "read_file" and result.read_text is not None and result.read_relpath is not None and len(result.read_text) >= MIN_ELIGIBLE_READ_CODEPOINTS:
                return {
                    "status": "FORK", "history": history, "tool_count": tool_count,
                    "elapsed": budget.used_seconds, "eligible_call": call,
                    "eligible_result": result, "remaining_calls": calls[idx + 1 :],
                }
            history.append(function_output_item(call.get("call_id"), result.output))


def execute_branch(*, client: ResponsesClient, pair: dict, workspace: Path, history_at_fork: list, common_tool_count: int, common_elapsed: float, eligible_call: dict, eligible_result: ToolResult, remaining_calls: list[dict], alter_eligible_result: bool):
    history = copy.deepcopy(history_at_fork)
    runtime = ToolRuntime(workspace)
    mapper = OperationalMapper(workspace)
    steps = []
    tool_count = common_tool_count
    budget = ActiveBudget(max(0.0, MAX_WALL_SECONDS - common_elapsed))

    delivery = OneShotEligibleReadDelivery()
    delivered = delivery.deliver(eligible_result.read_text, alter=alter_eligible_result)
    history.append(function_output_item(eligible_call.get("call_id"), delivered))
    steps.append(mapper.record_completed_tool(eligible_result.observation))

    for call in remaining_calls:
        if tool_count >= MAX_TOOL_CALLS or budget.remaining <= 0:
            break
        tool_count += 1
        result = _tool_active(runtime, budget, call.get("name"), safe_args(call))
        history.append(function_output_item(call.get("call_id"), result.output))
        steps.append(mapper.record_completed_tool(result.observation))

    termination = None
    while tool_count < MAX_TOOL_CALLS:
        if budget.remaining <= 0:
            termination = {"kind": "MAX_WALL_SECONDS", "tool_calls_total": tool_count, "active_seconds": MAX_WALL_SECONDS}
            break
        try:
            resp = _model_create_active(client, budget, instructions=pair["system_prompt"], history=history)
        except ResponsesAPIError as exc:
            raise model_failure(exc, client, "BRANCH_EXECUTION") from None
        history = append_response_output(history, resp)
        calls = function_calls(resp)
        if not calls:
            termination = {"kind": "FINAL_AGENT_ANSWER", "tool_calls_total": tool_count, "active_seconds": min(MAX_WALL_SECONDS, common_elapsed + budget.used_seconds)}
            break
        for call in calls:
            if tool_count >= MAX_TOOL_CALLS or budget.remaining <= 0:
                break
            tool_count += 1
            result = _tool_active(runtime, budget, call.get("name"), safe_args(call))
            history.append(function_output_item(call.get("call_id"), result.output))
            steps.append(mapper.record_completed_tool(result.observation))
        if budget.remaining <= 0:
            termination = {"kind": "MAX_WALL_SECONDS", "tool_calls_total": tool_count, "active_seconds": MAX_WALL_SECONDS}
            break
        if tool_count >= MAX_TOOL_CALLS:
            termination = {"kind": "MAX_TOOL_CALLS", "tool_calls_total": tool_count, "active_seconds": min(MAX_WALL_SECONDS, common_elapsed + budget.used_seconds)}
            break
    if termination is None:
        termination = {"kind": "MAX_TOOL_CALLS", "tool_calls_total": tool_count, "active_seconds": min(MAX_WALL_SECONDS, common_elapsed + budget.used_seconds)}
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

    private = Path(tempfile.mkdtemp(prefix=f"olp003-private-{pair_id}-"))
    client: ResponsesClient | None = None
    try:
        with zipfile.ZipFile(sealed_zip) as z:
            z.extractall(private / "sealed")
        secret_map = decrypt_map_in_memory(private / "sealed", key_path)
        mapping = {r["opaque_execution_id"]: r["condition"] for r in secret_map["conditions"] if r["pair_id"] == pair_id}
        if set(mapping) != set(opaque_ids) or sorted(mapping.values()) != ["CLEAN", "PERTURBED"]:
            raise PairInfrastructureFailure("ASSIGNMENT_INTEGRITY_FAILURE")
        try:
            key_path.unlink()
        except FileNotFoundError:
            pass
        try:
            sealed_zip.unlink()
        except FileNotFoundError:
            pass
        shutil.rmtree(private / "sealed", ignore_errors=True)

        temp_root = Path(tempfile.mkdtemp(prefix=f"olp003-pair-{pair_id}-"))
        try:
            rows = {}
            try:
                common = prepare_workspace(pair, temp_root)
            except PairInfrastructureFailure:
                for oid in opaque_ids:
                    rows[oid] = make_invalid_record(oid, pair_id, "CHECKPOINT_CANNOT_RESOLVE", client=None)
                receipt = {
                    "schema": "openline.paired-mechanism-benchmark.pair-verification.v1",
                    "experiment_id": EXPERIMENT_ID,
                    "benchmark_revision": BENCHMARK_REVISION,
                    "pair_id": pair_id,
                    "opaque_execution_ids": sorted(opaque_ids),
                    "pair_disposition": "PAIR_INSTRUMENTATION_INVALID",
                    "invalidity_reason": "CHECKPOINT_CANNOT_RESOLVE",
                    "benchmark_model_calls": 0,
                    "benchmark_completed_responses": 0,
                    "benchmark_retry_count": 0,
                    "returned_models": [],
                    "unblinded": False,
                }
                receipt.update(public_client_metrics(None))
                write_pair_outputs(out_dir, rows, receipt)
                return receipt

            # Construct the client only after workspace preparation. Its scheduler enforces
            # a fresh 45-second guard before this pair's first request, which also preserves
            # the cross-pair interval because matrix jobs run strictly one at a time.
            client = ResponsesClient(os.environ.get("OPENAI_API_KEY", ""))
            fork = execute_common_until_fork(client, pair, common)
            if fork["status"] != "FORK":
                reason = "NO_ELIGIBLE_READ_WITHIN_FIRST_20_COMMON_PREFIX_TOOL_CALLS"
                for oid in opaque_ids:
                    rows[oid] = make_invalid_record(oid, pair_id, reason, client=client)
                receipt = {
                    "schema": "openline.paired-mechanism-benchmark.pair-verification.v1",
                    "experiment_id": EXPERIMENT_ID,
                    "benchmark_revision": BENCHMARK_REVISION,
                    "pair_id": pair_id,
                    "opaque_execution_ids": sorted(opaque_ids),
                    "pair_disposition": "PAIR_INSTRUMENTATION_INVALID",
                    "invalidity_reason": reason,
                    "benchmark_model_calls": client.api_attempt_count,
                    "benchmark_completed_responses": client.completed_response_count,
                    "benchmark_retry_count": client.retry_count,
                    "returned_models": sorted(client.returned_models),
                    "unblinded": False,
                }
                receipt.update(public_client_metrics(client))
                write_pair_outputs(out_dir, rows, receipt)
                return receipt

            xdir, ydir = temp_root / "X", temp_root / "Y"
            clone_workspace(common, xdir); clone_workspace(common, ydir)
            dx, dy = workspace_bytes_digest(xdir), workspace_bytes_digest(ydir)
            if dx != dy:
                reason = "WORKSPACE_CANNOT_FORK_BYTE_IDENTICALLY"
                for oid in opaque_ids:
                    rows[oid] = make_invalid_record(oid, pair_id, reason, client=client)
                receipt = {
                    "schema": "openline.paired-mechanism-benchmark.pair-verification.v1",
                    "experiment_id": EXPERIMENT_ID,
                    "benchmark_revision": BENCHMARK_REVISION,
                    "pair_id": pair_id,
                    "opaque_execution_ids": sorted(opaque_ids),
                    "pair_disposition": "PAIR_INSTRUMENTATION_INVALID",
                    "invalidity_reason": reason,
                    "benchmark_model_calls": client.api_attempt_count,
                    "benchmark_completed_responses": client.completed_response_count,
                    "benchmark_retry_count": client.retry_count,
                    "returned_models": sorted(client.returned_models),
                    "unblinded": False,
                }
                receipt.update(public_client_metrics(client))
                write_pair_outputs(out_dir, rows, receipt)
                return receipt

            branch_results = {}
            for oid in opaque_ids:
                suffix = oid.rsplit("-", 1)[1]
                ws = xdir if suffix == "X" else ydir
                alter = mapping[oid] == "PERTURBED"
                steps, termination = execute_branch(
                    client=client,
                    pair=pair,
                    workspace=ws,
                    history_at_fork=fork["history"],
                    common_tool_count=fork["tool_count"],
                    common_elapsed=fork["elapsed"],
                    eligible_call=fork["eligible_call"],
                    eligible_result=fork["eligible_result"],
                    remaining_calls=fork["remaining_calls"],
                    alter_eligible_result=alter,
                )
                branch_results[oid] = (steps, termination)

            pair_invalid = any(len(steps) < 3 for steps, _ in branch_results.values())
            if pair_invalid:
                reason = "REQUIRED_FROZEN_SIGNAL_OBSERVATIONS_CANNOT_BE_EMITTED"
                for oid in opaque_ids:
                    rows[oid] = make_invalid_record(oid, pair_id, reason, client=client)
                disposition = "PAIR_INSTRUMENTATION_INVALID"
            else:
                for oid, (steps, term) in branch_results.items():
                    rows[oid] = make_trace(oid, pair_id, steps, term, client=client)
                disposition = "PAIR_VALID_FOR_BLIND_SCORING"

            receipt = {
                "schema": "openline.paired-mechanism-benchmark.pair-verification.v1",
                "experiment_id": EXPERIMENT_ID,
                "benchmark_revision": BENCHMARK_REVISION,
                "pair_id": pair_id,
                "opaque_execution_ids": sorted(opaque_ids),
                "pair_disposition": disposition,
                "benchmark_model_calls": client.api_attempt_count,
                "benchmark_completed_responses": client.completed_response_count,
                "benchmark_retry_count": client.retry_count,
                "api_failure_event_count": len(client.failure_events),
                "infrastructure_wait_seconds": client.infrastructure_wait_seconds,
                "active_api_seconds": client.active_api_seconds,
                "request_start_count": len(client.request_start_events),
                "rate_limit_header_observations": client.response_rate_limit_headers,
                "returned_models": sorted(client.returned_models),
                "reasoning_effort": REASONING_EFFORT,
                "fork_workspace_sha256": dx,
                "branch_config_equal": True,
                "unblinded": False,
            }
            receipt.update(public_client_metrics(client))
            if pair_invalid:
                receipt["invalidity_reason"] = "REQUIRED_FROZEN_SIGNAL_OBSERVATIONS_CANNOT_BE_EMITTED"
            assert_export_safe(receipt)
            write_pair_outputs(out_dir, rows, receipt)
            return receipt
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)
    except PairInfrastructureFailure as exc:
        if client is not None and "api_metrics" not in exc.detail:
            exc.detail["api_metrics"] = client.metrics()
        raise
    finally:
        shutil.rmtree(private, ignore_errors=True)


def write_infrastructure_receipt(out_dir: Path, pair_id: str, failure: PairInfrastructureFailure):
    out_dir.mkdir(parents=True, exist_ok=True)
    detail = dict(failure.detail)
    rec = {
        "schema": "openline.paired-mechanism-benchmark.infrastructure-failure.v2",
        "experiment_id": EXPERIMENT_ID,
        "benchmark_revision": BENCHMARK_REVISION,
        "pair_id": pair_id,
        "status": "EXECUTION_INFRASTRUCTURE_FAILURE",
        "failure_category": failure.reason,
        "failure_detail": detail,
        "condition_linked_interpretation": False,
        "unblinded": False,
    }
    assert_export_safe(rec)
    path = out_dir / f"{pair_id}.infrastructure.json"
    path.write_bytes(pretty_json_bytes(rec))
    (out_dir / f"{pair_id}.infrastructure.json.sha256").write_text(f"{sha256_file(path)}  {pair_id}.infrastructure.json\n", encoding="utf-8")
    return rec


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
    except PairInfrastructureFailure as exc:
        rec = write_infrastructure_receipt(Path(args.out_dir), args.pair_id, exc)
        print(json.dumps({"pair_id": args.pair_id, "status": rec["status"], "failure_category": rec["failure_category"]}, indent=2))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
