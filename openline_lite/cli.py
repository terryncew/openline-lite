"""Command-line surface for OpenLine Lite."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .canonical import dumps, loads, pretty, sha256_hex
from .chain import verify_native_chain
from .crypto import generate_private_key_hex, public_key_hex
from .gate import ReceiptGate
from .gateway import EvidenceGateway, NativeOLPAdapter
from .handoff import build_handoff_projection
from .mapped_adapter import AdapterProfile, MappedEd25519JSONAdapter
from .policy import Policy
from .wire import SOURCE_SCHEMA, issue_source_receipt, verify_decision_receipt


MAX_CONTROL_BYTES = 1_048_576
MAX_ARTIFACT_BYTES = 8_388_608
MAX_TOTAL_ARTIFACT_BYTES = 33_554_432
MAX_MANIFEST_FILES = 512
MAX_MANIFEST_TOTAL_BYTES = 16_777_216


def _read_bytes(path: Path, *, maximum: int = MAX_CONTROL_BYTES) -> bytes:
    size = path.stat().st_size
    if size > maximum:
        raise ValueError(f"file_size_limit_exceeded:{path}")
    return path.read_bytes()


def _read_json(path: Path) -> dict[str, Any]:
    value = loads(_read_bytes(path))
    if not isinstance(value, dict):
        raise ValueError(f"object_required:{path}")
    return value


def _read_json_list(path: Path) -> list[Any]:
    value = loads(_read_bytes(path))
    if not isinstance(value, list):
        raise ValueError(f"array_required:{path}")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(pretty(value), encoding="utf-8")


def _key(path: Path) -> str:
    return _read_bytes(path, maximum=1_024).decode("ascii").strip()


def _load_artifacts(manifest_path: Path | None) -> dict[str, bytes]:
    if manifest_path is None:
        return {}
    manifest = _read_json(manifest_path)
    if len(manifest) > MAX_MANIFEST_FILES:
        raise ValueError("evidence_manifest_count_limit_exceeded")
    base = manifest_path.resolve().parent
    artifacts: dict[str, bytes] = {}
    total = 0
    for evidence_id, relative in manifest.items():
        if not isinstance(evidence_id, str) or not isinstance(relative, str):
            raise ValueError("evidence_manifest_invalid")
        candidate = (base / relative).resolve()
        try:
            candidate.relative_to(base)
        except ValueError as exc:
            raise ValueError(f"evidence_path_escape:{evidence_id}") from exc
        artifact = _read_bytes(candidate, maximum=MAX_ARTIFACT_BYTES)
        total += len(artifact)
        if total > MAX_TOTAL_ARTIFACT_BYTES:
            raise ValueError("evidence_total_size_limit_exceeded")
        artifacts[evidence_id] = artifact
    return artifacts


def _load_manifest_files(
    manifest_path: Path,
    *,
    maximum_files: int = MAX_MANIFEST_FILES,
    maximum_each: int = MAX_CONTROL_BYTES,
    maximum_total: int = MAX_MANIFEST_TOTAL_BYTES,
) -> list[bytes]:
    relative_paths = _read_json_list(manifest_path)
    if len(relative_paths) > maximum_files:
        raise ValueError("file_manifest_count_limit_exceeded")
    base = manifest_path.resolve().parent
    output: list[bytes] = []
    total = 0
    for index, relative in enumerate(relative_paths):
        if not isinstance(relative, str):
            raise ValueError(f"file_manifest_invalid:{index}")
        candidate = (base / relative).resolve()
        try:
            candidate.relative_to(base)
        except ValueError as exc:
            raise ValueError(f"file_manifest_path_escape:{index}") from exc
        item = _read_bytes(candidate, maximum=maximum_each)
        total += len(item)
        if total > maximum_total:
            raise ValueError("file_manifest_total_size_limit_exceeded")
        output.append(item)
    return output


def _gateway(adapter_profile_path: str | None) -> EvidenceGateway:
    adapters = [NativeOLPAdapter()]
    if adapter_profile_path:
        profile = AdapterProfile.from_mapping(_read_json(Path(adapter_profile_path)))
        adapters.append(MappedEd25519JSONAdapter(profile))
    return EvidenceGateway(adapters)


def _parse_now(value: str | None) -> datetime | None:
    if value is None:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("now_timezone_missing")
    return parsed.astimezone(timezone.utc)


def command_keygen(args: argparse.Namespace) -> int:
    path = Path(args.out)
    if path.exists():
        raise ValueError("key_exists")
    private = generate_private_key_hex()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="ascii") as handle:
        handle.write(private + "\n")
    print(public_key_hex(private))
    return 0


def command_issue(args: argparse.Namespace) -> int:
    payload = _read_json(Path(args.payload))
    receipt = issue_source_receipt(payload, _key(Path(args.key)), args.key_id)
    _write_json(Path(args.out), receipt)
    print(sha256_hex(dumps(receipt)))
    return 0


def command_decide(args: argparse.Namespace) -> int:
    trust = _read_json(Path(args.trust))
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in trust.items()):
        raise ValueError("trust_store_invalid")
    source_bytes = _read_bytes(Path(args.source))
    intake = _gateway(args.adapter_profile).inspect(
        source_bytes,
        source_format=args.source_format,
        trusted_keys=dict(trust),
    )
    policy = Policy.from_mapping(_read_json(Path(args.policy)))
    result = ReceiptGate(
        gate_id=args.gate_id, private_key=_key(Path(args.gate_key))
    ).decide(
        intake,
        artifacts=_load_artifacts(Path(args.evidence) if args.evidence else None),
        policy=policy,
        now=_parse_now(args.now),
        side_effect_observed=args.side_effect_observed,
    )
    _write_json(Path(args.out), result.receipt)
    print(
        json.dumps(
            {
                "verdict": result.verdict,
                "decision": result.decision,
                "reason_codes": result.reason_codes,
            }
        )
    )
    return 0 if result.decision == "COMMIT" else 1


def command_inspect(args: argparse.Namespace) -> int:
    trust = _read_json(Path(args.trust))
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in trust.items()):
        raise ValueError("trust_store_invalid")
    intake = _gateway(args.adapter_profile).inspect(
        _read_bytes(Path(args.source)),
        source_format=args.source_format,
        trusted_keys=dict(trust),
    )
    print(pretty(intake.to_dict()), end="")
    statuses = {
        intake.integrity.status,
        intake.provenance.status,
        intake.normalization.status,
    }
    return 0 if statuses == {"pass"} else 1


def command_verify(args: argparse.Namespace) -> int:
    receipt = _read_json(Path(args.receipt))
    result = verify_decision_receipt(receipt, {args.gate_id: args.gate_public_key})
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 2


def command_demo(args: argparse.Namespace) -> int:
    from .demo import run_demo

    print(json.dumps(run_demo(), indent=2, sort_keys=True))
    return 0


def command_verify_chain(args: argparse.Namespace) -> int:
    trust = _read_json(Path(args.trust))
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in trust.items()):
        raise ValueError("trust_store_invalid")
    result = verify_native_chain(
        _load_manifest_files(Path(args.manifest), maximum_files=args.max_items),
        dict(trust),
        max_items=args.max_items,
    )
    print(pretty(result.to_dict()), end="")
    return 0 if result.valid else 1


def command_handoff(args: argparse.Namespace) -> int:
    producer_trust = _read_json(Path(args.producer_trust))
    gate_trust = _read_json(Path(args.gate_trust))
    if not all(
        isinstance(k, str) and isinstance(v, str)
        for store in (producer_trust, gate_trust)
        for k, v in store.items()
    ):
        raise ValueError("trust_store_invalid")
    chain = verify_native_chain(
        _load_manifest_files(Path(args.chain), maximum_files=args.max_chain_items),
        dict(producer_trust),
        max_items=args.max_chain_items,
    )
    decisions = []
    for raw in _load_manifest_files(Path(args.decisions)):
        value = loads(raw)
        if not isinstance(value, dict):
            raise ValueError("decision_receipt_object_required")
        decisions.append(value)
    policies = [Policy.from_mapping(_read_json(Path(path))) for path in args.policy]
    projection = build_handoff_projection(
        chain,
        decisions,
        gate_trust,
        allowed_policy_hashes={policy.sha256 for policy in policies},
        max_claims=args.max_claims,
        max_claim_chars=args.max_claim_chars,
    )
    rendered = (
        pretty(projection.to_dict())
        if args.format == "json"
        else projection.render_jsonl()
    )
    if args.out:
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


def command_benchmark(args: argparse.Namespace) -> int:
    from .benchmark import run_benchmark

    try:
        depths = [int(value) for value in args.depths.split(",")]
    except ValueError as exc:
        raise ValueError("benchmark_depths_invalid") from exc
    result = run_benchmark(
        depths=depths,
        iterations=args.iterations,
        tokenizer_name=args.tokenizer,
        max_claims=args.max_claims,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="olp-lite")
    sub = parser.add_subparsers(dest="command", required=True)

    keygen = sub.add_parser("keygen")
    keygen.add_argument("--out", required=True)
    keygen.set_defaults(func=command_keygen)

    issue = sub.add_parser("issue")
    issue.add_argument("--payload", required=True)
    issue.add_argument("--key", required=True)
    issue.add_argument("--key-id", required=True)
    issue.add_argument("--out", required=True)
    issue.set_defaults(func=command_issue)

    decide = sub.add_parser("decide")
    decide.add_argument("--source", required=True)
    decide.add_argument("--source-format", default=SOURCE_SCHEMA)
    decide.add_argument("--adapter-profile")
    decide.add_argument("--trust", required=True)
    decide.add_argument("--policy", required=True)
    decide.add_argument("--evidence")
    decide.add_argument("--gate-key", required=True)
    decide.add_argument("--gate-id", required=True)
    decide.add_argument("--out", required=True)
    decide.add_argument(
        "--now", help="ISO-8601 evaluation time; intended for deterministic tests"
    )
    decide.add_argument("--side-effect-observed", action="store_true")
    decide.set_defaults(func=command_decide)

    inspect_parser = sub.add_parser(
        "inspect", help="inspect source integrity, trust, and normalization"
    )
    inspect_parser.add_argument("--source", required=True)
    inspect_parser.add_argument("--source-format", default=SOURCE_SCHEMA)
    inspect_parser.add_argument("--adapter-profile")
    inspect_parser.add_argument("--trust", required=True)
    inspect_parser.set_defaults(func=command_inspect)

    verify_parser = sub.add_parser("verify-decision")
    verify_parser.add_argument("--receipt", required=True)
    verify_parser.add_argument("--gate-id", required=True)
    verify_parser.add_argument("--gate-public-key", required=True)
    verify_parser.set_defaults(func=command_verify)

    demo_parser = sub.add_parser(
        "demo", help="run the local six-case hostile-control demo"
    )
    demo_parser.set_defaults(func=command_demo)

    chain_parser = sub.add_parser(
        "verify-chain", help="verify a complete native receipt chain"
    )
    chain_parser.add_argument(
        "--manifest", required=True, help="JSON array of receipt paths"
    )
    chain_parser.add_argument("--trust", required=True)
    chain_parser.add_argument("--max-items", type=int, default=256)
    chain_parser.set_defaults(func=command_verify_chain)

    handoff_parser = sub.add_parser(
        "handoff", help="render a compact verified prompt handoff"
    )
    handoff_parser.add_argument(
        "--chain", required=True, help="JSON array of source receipt paths"
    )
    handoff_parser.add_argument(
        "--decisions", required=True, help="JSON array of decision receipt paths"
    )
    handoff_parser.add_argument("--producer-trust", required=True)
    handoff_parser.add_argument("--gate-trust", required=True)
    handoff_parser.add_argument(
        "--policy",
        action="append",
        required=True,
        help="receiver policy file whose exact hash may authorize inclusion; repeatable",
    )
    handoff_parser.add_argument("--max-claims", type=int, default=3)
    handoff_parser.add_argument("--max-claim-chars", type=int, default=280)
    handoff_parser.add_argument("--max-chain-items", type=int, default=256)
    handoff_parser.add_argument("--format", choices=("jsonl", "json"), default="jsonl")
    handoff_parser.add_argument("--out")
    handoff_parser.set_defaults(func=command_handoff)

    benchmark_parser = sub.add_parser(
        "benchmark",
        help="measure full-history versus verified-handoff cost and decisions",
    )
    benchmark_parser.add_argument("--depths", default="1,2,4,8,16,32")
    benchmark_parser.add_argument("--iterations", type=int, default=20)
    benchmark_parser.add_argument("--max-claims", type=int, default=3)
    benchmark_parser.add_argument(
        "--tokenizer",
        default="lexical",
        help="lexical or tiktoken:<encoding>, for example tiktoken:cl100k_base",
    )
    benchmark_parser.add_argument("--out")
    benchmark_parser.set_defaults(func=command_benchmark)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        return int(args.func(args))
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
