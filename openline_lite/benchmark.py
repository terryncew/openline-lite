"""Transparent local benchmark for lightweight verified handoffs.

The benchmark keeps prompt tokens, stored bytes, latency, and decision
correctness separate.  It does not invent an aggregate score.
"""

from __future__ import annotations

import copy
import platform
import re
import statistics
import sys
import time
import tracemalloc
from datetime import datetime, timedelta, timezone
from importlib.metadata import version
from typing import Any, Callable

from . import __version__
from .canonical import dumps, sha256_hex
from .chain import verify_native_chain
from .crypto import public_key_hex
from .gate import ReceiptGate
from .gateway import EvidenceGateway
from .handoff import build_handoff_projection
from .policy import Policy
from .wire import envelope_hash, issue_source_receipt


PRODUCER_KEY = "33" * 32
GATE_KEY = "44" * 32
BASE_TIME = datetime(2026, 7, 17, 19, 0, tzinfo=timezone.utc)


def _token_counter(name: str) -> tuple[str, Callable[[str], int]]:
    if name == "lexical":
        return "lexical", lambda text: len(re.findall(r"\w+|[^\w\s]", text))
    if name.startswith("tiktoken:"):
        encoding_name = name.split(":", 1)[1]
        if not encoding_name:
            raise ValueError("benchmark_tokenizer_invalid")
        try:
            import tiktoken
        except ImportError as exc:
            raise ValueError(
                "benchmark_tokenizer_dependency_missing:install openline-lite[benchmark]"
            ) from exc
        encoding = tiktoken.get_encoding(encoding_name)
        return name, lambda text: len(encoding.encode(text))
    raise ValueError("benchmark_tokenizer_invalid")


def _percent_reduction(baseline: int, candidate: int) -> float:
    if baseline == 0:
        return 0.0
    return round((baseline - candidate) * 100.0 / baseline, 1)


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * fraction + 0.999999)))
    return round(ordered[index], 3)


def _policy(step: int | None = None) -> Policy:
    claim_rules: list[dict[str, Any]] = [
        {
            "id": "step-ok",
            "evidence_id": "result",
            "pointer": "/ok",
            "expected": True,
        }
    ]
    if step is not None:
        claim_rules.extend(
            [
                {
                    "id": "step-number",
                    "evidence_id": "result",
                    "pointer": "/step",
                    "expected": step,
                },
                {
                    "id": "step-value",
                    "evidence_id": "result",
                    "pointer": "/value",
                    "expected": step * step,
                },
            ]
        )
    return Policy.from_mapping(
        {
            "policy_id": f"lite-benchmark-step-{step}"
            if step is not None
            else "lite-benchmark-quality",
            "version": "3",
            "allowed_actions": ["tool_call"],
            "required_evidence": ["result"],
            "claim_rules": claim_rules,
            "max_age_seconds": 3600,
            "on_undecidable": "QUARANTINE",
            "rollback_supported": False,
        }
    )


def _trace_step(index: int, evidence: bytes) -> str:
    value = index * index
    return (
        f"STEP {index}\n"
        f"plan: compute the deterministic value for work item {index}, preserve the result, "
        "and tell the next agent whether the tool completed successfully.\n"
        f'tool_call: {{"name":"compute_item","arguments":{{"item":{index},"mode":"safe"}}}}\n'
        f"tool_result: {evidence.decode('utf-8')}\n"
        f"assistant_result: work item {index} completed; value={value}; safe to continue.\n"
    )


def _unsigned_projection(items: list[dict[str, Any]], *, accepted_count: int) -> str:
    """Render the same selected data without a verification signal.

    This control makes the benchmark separate compression from appraisal.  It
    receives the same bounded items and support facts as the verified prompt,
    but omits the chain tip and approved policy identifiers. It is an optimistic
    lower bound for compression-only handoff cost, not a safe decision path.
    """

    header = {
        "s": "unsigned.compact.v1",
        "k": "h",
        "ok": accepted_count,
        "show": len(items),
    }
    lines = [dumps(header).decode("utf-8")]
    lines.extend(dumps({"k": "i", **item}).decode("utf-8") for item in items)
    return "\n".join(lines) + "\n"


def _build_run(depth: int) -> dict[str, Any]:
    producer_trust = {"benchmark-producer": public_key_hex(PRODUCER_KEY)}
    gate_trust = {"benchmark-gate": public_key_hex(GATE_KEY)}
    gateway = EvidenceGateway()
    gate = ReceiptGate(gate_id="benchmark-gate", private_key=GATE_KEY)

    source_receipts: list[bytes] = []
    decision_receipts: list[dict[str, Any]] = []
    evidence_items: list[bytes] = []
    policy_hashes: set[str] = set()
    transcript_steps: list[str] = []
    event_wall_ms: list[float] = []
    previous_envelope: dict[str, Any] | None = None

    for index in range(depth):
        policy = _policy(index)
        policy_hashes.add(policy.sha256)
        evidence = dumps({"ok": True, "step": index, "value": index * index})
        payload: dict[str, Any] = {
            "schema": "olp.source.v1",
            "issuer": "benchmark-agent",
            "issued_at": (BASE_TIME + timedelta(seconds=index))
            .isoformat()
            .replace("+00:00", "Z"),
            "run_id": f"benchmark-depth-{depth}",
            "sequence": index,
            "action": {"type": "tool_call", "name": "compute_item"},
            "claim": f"Work item {index} completed with value {index * index}.",
            "evidence": [{"id": "result", "sha256": sha256_hex(evidence)}],
        }
        if previous_envelope is not None:
            payload["parent_hash"] = envelope_hash(previous_envelope)

        started = time.perf_counter_ns()
        envelope = issue_source_receipt(payload, PRODUCER_KEY, "benchmark-producer")
        source_bytes = dumps(envelope)
        intake = gateway.inspect(
            source_bytes,
            source_format="olp.source.v1",
            trusted_keys=producer_trust,
        )
        decision = gate.decide(
            intake,
            artifacts={"result": evidence},
            policy=policy,
            now=BASE_TIME + timedelta(seconds=depth + 1),
        )
        event_wall_ms.append((time.perf_counter_ns() - started) / 1_000_000)

        source_receipts.append(source_bytes)
        decision_receipts.append(decision.receipt)
        evidence_items.append(evidence)
        transcript_steps.append(_trace_step(index, evidence))
        previous_envelope = envelope

    return {
        "producer_trust": producer_trust,
        "gate_trust": gate_trust,
        "sources": source_receipts,
        "decisions": decision_receipts,
        "evidence": evidence_items,
        "policy_hashes": policy_hashes,
        "transcript_steps": transcript_steps,
        "event_wall_ms": event_wall_ms,
    }


def _cost_row(
    depth: int,
    *,
    iterations: int,
    max_claims: int,
    count_tokens: Callable[[str], int],
) -> dict[str, Any]:
    run = _build_run(depth)
    full_context = "\n".join(run["transcript_steps"])
    chain = verify_native_chain(run["sources"], run["producer_trust"])
    projection = build_handoff_projection(
        chain,
        run["decisions"],
        run["gate_trust"],
        allowed_policy_hashes=run["policy_hashes"],
        max_claims=max_claims,
    )
    handoff_context = projection.render_jsonl()
    unsigned_context = _unsigned_projection(
        [item.to_prompt_dict() for item in projection.items],
        accepted_count=projection.accepted_count,
    )

    cumulative_full = 0
    cumulative_unsigned = 0
    cumulative_handoff = 0
    for end in range(1, depth + 1):
        cumulative_full += count_tokens("\n".join(run["transcript_steps"][:end]))
        prefix_chain = verify_native_chain(run["sources"][:end], run["producer_trust"])
        prefix_projection = build_handoff_projection(
            prefix_chain,
            run["decisions"][:end],
            run["gate_trust"],
            allowed_policy_hashes=run["policy_hashes"],
            max_claims=max_claims,
        )
        cumulative_unsigned += count_tokens(
            _unsigned_projection(
                [item.to_prompt_dict() for item in prefix_projection.items],
                accepted_count=prefix_projection.accepted_count,
            )
        )
        cumulative_handoff += count_tokens(prefix_projection.render_jsonl())

    wall_ms: list[float] = []
    cpu_ms: list[float] = []
    for _ in range(iterations):
        wall_started = time.perf_counter_ns()
        cpu_started = time.process_time_ns()
        measured_chain = verify_native_chain(run["sources"], run["producer_trust"])
        build_handoff_projection(
            measured_chain,
            run["decisions"],
            run["gate_trust"],
            allowed_policy_hashes=run["policy_hashes"],
            max_claims=max_claims,
        )
        cpu_ms.append((time.process_time_ns() - cpu_started) / 1_000_000)
        wall_ms.append((time.perf_counter_ns() - wall_started) / 1_000_000)

    tracemalloc.start()
    memory_chain = verify_native_chain(run["sources"], run["producer_trust"])
    build_handoff_projection(
        memory_chain,
        run["decisions"],
        run["gate_trust"],
        allowed_policy_hashes=run["policy_hashes"],
        max_claims=max_claims,
    )
    _, peak_python_alloc_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    full_tokens = count_tokens(full_context)
    unsigned_tokens = count_tokens(unsigned_context)
    handoff_tokens = count_tokens(handoff_context)
    receipt_bytes = sum(len(item) for item in run["sources"]) + sum(
        len(dumps(item)) for item in run["decisions"]
    )
    return {
        "depth": depth,
        "max_claims_in_prompt": max_claims,
        "one_handoff": {
            "full_history_prompt_tokens": full_tokens,
            "unsigned_compact_prompt_tokens": unsigned_tokens,
            "verified_handoff_prompt_tokens": handoff_tokens,
            "prompt_token_reduction_percent": _percent_reduction(
                full_tokens, handoff_tokens
            ),
            "unsigned_token_reduction_percent": _percent_reduction(
                full_tokens, unsigned_tokens
            ),
            "verified_overhead_tokens_vs_unsigned": handoff_tokens - unsigned_tokens,
            "full_history_prompt_bytes": len(full_context.encode("utf-8")),
            "unsigned_compact_prompt_bytes": len(unsigned_context.encode("utf-8")),
            "verified_handoff_prompt_bytes": len(handoff_context.encode("utf-8")),
        },
        "cumulative_at_every_handoff": {
            "full_history_prompt_tokens": cumulative_full,
            "unsigned_compact_prompt_tokens": cumulative_unsigned,
            "verified_handoff_prompt_tokens": cumulative_handoff,
            "prompt_token_reduction_percent": _percent_reduction(
                cumulative_full, cumulative_handoff
            ),
            "unsigned_token_reduction_percent": _percent_reduction(
                cumulative_full, cumulative_unsigned
            ),
            "verified_overhead_tokens_vs_unsigned": cumulative_handoff
            - cumulative_unsigned,
        },
        "local_storage_and_evidence": {
            "receipt_bytes_outside_prompt": receipt_bytes,
            "evidence_bytes_outside_prompt": sum(len(item) for item in run["evidence"]),
            "evidence_items_checked_during_issue_and_gate": len(run["evidence"]),
            "evidence_reads_during_verify_and_project": 0,
            "verify_and_project_peak_python_alloc_bytes": peak_python_alloc_bytes,
        },
        "latency_ms": {
            "issue_and_gate_per_event_p50": round(
                statistics.median(run["event_wall_ms"]), 3
            ),
            "verify_chain_and_project_p50": round(statistics.median(wall_ms), 3),
            "verify_chain_and_project_p95": _percentile(wall_ms, 0.95),
            "verify_chain_and_project_cpu_p50": round(statistics.median(cpu_ms), 3),
        },
    }


def _quality_cases() -> dict[str, Any]:
    producer_trust = {"quality-producer": public_key_hex(PRODUCER_KEY)}
    gateway = EvidenceGateway()
    gate = ReceiptGate(gate_id="quality-gate", private_key=GATE_KEY)
    policy = _policy()
    supporting = dumps({"ok": True})
    contradicting = dumps({"ok": False})

    def payload(evidence: bytes) -> dict[str, Any]:
        return {
            "schema": "olp.source.v1",
            "issuer": "quality-agent",
            "issued_at": "2026-07-17T18:59:30Z",
            "run_id": "quality-run",
            "sequence": 0,
            "action": {"type": "tool_call", "name": "check"},
            "claim": "The check passed.",
            "evidence": [{"id": "result", "sha256": sha256_hex(evidence)}],
        }

    valid_envelope = issue_source_receipt(
        payload(supporting), PRODUCER_KEY, "quality-producer"
    )
    contradiction_envelope = issue_source_receipt(
        payload(contradicting), PRODUCER_KEY, "quality-producer"
    )
    tampered_envelope = copy.deepcopy(valid_envelope)
    tampered_envelope["payload"]["claim"] = "Changed after signing."

    disallowed_policy = Policy.from_mapping(
        {
            **policy.to_dict(),
            "policy_id": "quality-disallowed",
            "allowed_actions": ["memory_write"],
        }
    )
    future_payload = payload(supporting)
    future_payload["issued_at"] = "2026-07-17T20:00:00Z"
    future_envelope = issue_source_receipt(
        future_payload, PRODUCER_KEY, "quality-producer"
    )
    cases = {
        "valid": (
            valid_envelope,
            {"result": supporting},
            policy,
            BASE_TIME,
            producer_trust,
            "COMMIT",
        ),
        "missing_evidence": (
            valid_envelope,
            {},
            policy,
            BASE_TIME,
            producer_trust,
            "QUARANTINE",
        ),
        "signed_unsupported": (
            contradiction_envelope,
            {"result": contradicting},
            policy,
            BASE_TIME,
            producer_trust,
            "DENY",
        ),
        "tampered_signature": (
            tampered_envelope,
            {"result": supporting},
            policy,
            BASE_TIME,
            producer_trust,
            "DENY",
        ),
        "evidence_hash_mismatch": (
            valid_envelope,
            {"result": b'{"ok":false}'},
            policy,
            BASE_TIME,
            producer_trust,
            "DENY",
        ),
        "policy_disallowed": (
            valid_envelope,
            {"result": supporting},
            disallowed_policy,
            BASE_TIME,
            producer_trust,
            "DENY",
        ),
        "expired": (
            valid_envelope,
            {"result": supporting},
            policy,
            BASE_TIME + timedelta(hours=2),
            producer_trust,
            "QUARANTINE",
        ),
        "untrusted_source": (
            valid_envelope,
            {"result": supporting},
            policy,
            BASE_TIME,
            {},
            "QUARANTINE",
        ),
        "future_timestamp": (
            future_envelope,
            {"result": supporting},
            policy,
            BASE_TIME,
            producer_trust,
            "DENY",
        ),
    }
    rows: dict[str, Any] = {}
    signature_only_correct = 0
    openline_correct = 0

    for name, (
        envelope,
        artifacts,
        active_policy,
        now,
        trust,
        expected,
    ) in cases.items():
        intake = gateway.inspect(
            dumps(envelope),
            source_format="olp.source.v1",
            trusted_keys=trust,
        )
        source_statuses = [
            intake.integrity.status,
            intake.provenance.status,
            intake.normalization.status,
        ]
        signature_only = (
            "DENY"
            if "fail" in source_statuses
            else "QUARANTINE"
            if "unavailable" in source_statuses
            else "COMMIT"
        )
        result = gate.decide(
            intake,
            artifacts=artifacts,
            policy=active_policy,
            now=now,
        )
        signature_only_correct += int(signature_only == expected)
        openline_correct += int(result.decision == expected)
        rows[name] = {
            "expected": expected,
            "signature_only": signature_only,
            "openline_lite": result.decision,
        }

    return {
        "case_count": len(cases),
        "signature_only_correct": signature_only_correct,
        "openline_lite_correct": openline_correct,
        "signature_only_accuracy_percent": round(
            signature_only_correct * 100 / len(cases), 1
        ),
        "openline_lite_accuracy_percent": round(openline_correct * 100 / len(cases), 1),
        "cases": rows,
    }


def _first_break_even(rows: list[dict[str, Any]], *, section: str) -> int | None:
    """Return the first tested depth where verified carryover beats full history."""

    for row in rows:
        metrics = row[section]
        if (
            metrics["verified_handoff_prompt_tokens"]
            <= metrics["full_history_prompt_tokens"]
        ):
            return int(row["depth"])
    return None


def run_benchmark(
    *,
    depths: list[int],
    iterations: int = 20,
    tokenizer_name: str = "lexical",
    max_claims: int = 3,
) -> dict[str, Any]:
    if not depths or any(depth < 1 for depth in depths):
        raise ValueError("benchmark_depths_invalid")
    if iterations < 1:
        raise ValueError("benchmark_iterations_invalid")
    if max_claims < 1:
        raise ValueError("benchmark_max_claims_invalid")
    tokenizer, count_tokens = _token_counter(tokenizer_name)
    rows = [
        _cost_row(
            depth,
            iterations=iterations,
            max_claims=max_claims,
            count_tokens=count_tokens,
        )
        for depth in sorted(set(depths))
    ]
    break_even = {
        "tested_depths": [row["depth"] for row in rows],
        "one_handoff_first_tested_depth": _first_break_even(
            rows, section="one_handoff"
        ),
        "cumulative_first_tested_depth": _first_break_even(
            rows, section="cumulative_at_every_handoff"
        ),
        "meaning": (
            "The first tested depth where verified prompt tokens are less than or "
            "equal to full-history prompt tokens. This is not an interpolated universal "
            "threshold; rerun it on your own traces and tokenizer."
        ),
    }
    environment = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "openline_lite": __version__,
        "cryptography": version("cryptography"),
    }
    if tokenizer.startswith("tiktoken:"):
        environment["tiktoken"] = version("tiktoken")

    return {
        "schema": "olp.lite-benchmark.v1",
        "benchmark": "synthetic_lightweight_handoff",
        "tokenizer": tokenizer,
        "iterations_per_depth": iterations,
        "environment": environment,
        "prompt_scope": (
            "Counts only text inserted into the next model prompt. Receipts and evidence stay "
            "outside the prompt and are reported separately as stored bytes."
        ),
        "retention_policy": (
            f"The complete receipt chain is retained locally; the prompt projection includes "
            f"only the latest {max_claims} accepted action-and-fact items."
        ),
        "quality_scope": (
            "Disposition correctness on nine declared policy fixtures, not general model-output quality."
        ),
        "evidence_scope": (
            "Evidence is checked once when the receiver issues each gate decision. "
            "The handoff verifier trusts pinned receiver-signed decisions and does not "
            "re-open evidence; full independent replay requires the retained bundle."
        ),
        "interpretation": (
            "Bounded selection creates the prompt-token reduction after a measurable "
            "break-even depth. The unsigned compact control shows that compression alone "
            "is cheaper. OpenLine Lite adds local chain and claim-support verification "
            "before facts enter the compact handoff; at shallow depths that metadata can "
            "cost more tokens than carrying the short history."
        ),
        "cost_by_depth": rows,
        "break_even": break_even,
        "decision_quality": _quality_cases(),
        "combined_score": None,
    }
