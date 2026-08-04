from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .canonical import json_safe, sha256_file, write_json
from .data_io import SOURCE_COLUMNS, iter_rows
from .evaluate import evaluate_prefix
from .features import extract_all_prefixes
from .nebius import blind_record, label_row, sanitize_row

ROOT = Path(__file__).resolve().parents[2]
FEATURE_FILE = "features_blind.csv"
LABEL_FILE = "labels_sealed.csv"
BINDING_FILE = "FEATURE_LABEL_BINDING.json"
STATUS_FILE = "METRIC_AVAILABILITY.json"
_ALLOWED_DISPOSITIONS = {
    "CD_ADDS_HELDOUT_SIGNAL",
    "BASELINE_OUTPERFORMS_CD",
    "BASELINE_EQUIVALENT",
    "NO_RELIABLE_SIGNAL",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _manifest_expected_hashes(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    files = value.get("files")
    if not isinstance(files, dict):
        raise ValueError("data manifest must contain files object")
    expected: dict[str, str] = {}
    for name, metadata in files.items():
        if not isinstance(metadata, dict) or not isinstance(metadata.get("sha256"), str):
            raise ValueError(f"invalid data manifest entry: {name}")
        expected[name] = metadata["sha256"]
    return expected


def _source_key(path: Path) -> str:
    return f"data/{path.name}"


def prepare(input_paths: list[Path], output: Path, *, data_manifest: Path | None = None) -> dict[str, Any]:
    if not input_paths:
        raise ValueError("at least one input is required")
    if len({path.resolve() for path in input_paths}) != len(input_paths):
        raise ValueError("duplicate input paths")
    for path in input_paths:
        if not path.is_file():
            raise FileNotFoundError(path)

    expected_hashes = _manifest_expected_hashes(data_manifest)
    source_hashes: dict[str, str] = {}
    for path in input_paths:
        key = _source_key(path)
        actual = sha256_file(path)
        expected = expected_hashes.get(key)
        if expected_hashes and expected is None:
            raise ValueError(f"input absent from data manifest: {key}")
        if expected is not None and actual != expected:
            raise ValueError(f"data hash mismatch for {key}: {actual} != {expected}")
        source_hashes[key] = actual

    output.mkdir(parents=True, exist_ok=True)
    feature_path = output / FEATURE_FILE
    label_path = output / LABEL_FILE
    feature_tmp = feature_path.with_suffix(".csv.tmp")
    label_tmp = label_path.with_suffix(".csv.tmp")

    feature_writer: csv.DictWriter | None = None
    status_values: dict[str, str] | None = None
    feature_rows = 0
    label_rows = 0
    duplicate_trajectories = 0
    seen_trajectories: set[str] = set()

    with feature_tmp.open("w", newline="", encoding="utf-8") as feature_stream, label_tmp.open(
        "w", newline="", encoding="utf-8"
    ) as label_stream:
        label_writer = csv.DictWriter(label_stream, fieldnames=["trajectory_id", "instance_id", "target"])
        label_writer.writeheader()
        for path in input_paths:
            for row in iter_rows(path, columns=SOURCE_COLUMNS):
                label = label_row(row)
                trajectory_id = label["trajectory_id"]
                if trajectory_id in seen_trajectories:
                    duplicate_trajectories += 1
                    continue
                seen_trajectories.add(trajectory_id)
                label_writer.writerow(label)
                label_rows += 1

                blind = blind_record(sanitize_row(row))
                for feature in extract_all_prefixes(blind):
                    statuses = {name: value for name, value in feature.items() if name.endswith("_status")}
                    numeric_feature = {name: value for name, value in feature.items() if not name.endswith("_status")}
                    if status_values is None:
                        status_values = statuses
                    elif statuses != status_values:
                        raise ValueError("metric availability statuses changed across rows")
                    if feature_writer is None:
                        feature_writer = csv.DictWriter(feature_stream, fieldnames=list(numeric_feature))
                        feature_writer.writeheader()
                    feature_writer.writerow(numeric_feature)
                    feature_rows += 1

    if feature_writer is None or status_values is None or label_rows == 0:
        feature_tmp.unlink(missing_ok=True)
        label_tmp.unlink(missing_ok=True)
        raise ValueError("no usable trajectory rows")
    feature_tmp.replace(feature_path)
    label_tmp.replace(label_path)

    availability = {
        "schema": "coherence-dynamics.public-trajectory.metric-availability.v1",
        "statuses": status_values,
        "rows_share_identical_status": True,
    }
    availability_hash = write_json(output / STATUS_FILE, availability)
    receipt = {
        "schema": "coherence-dynamics.public-trajectory.feature-label-binding.v2",
        "created_at_utc": _utc_now(),
        "source_columns_loaded": list(SOURCE_COLUMNS),
        "source_hashes": source_hashes,
        "data_manifest_sha256": sha256_file(data_manifest) if data_manifest else None,
        "unique_trajectories": label_rows,
        "duplicate_trajectories_skipped": duplicate_trajectories,
        "feature_rows": feature_rows,
        "prefixes_per_trajectory": feature_rows // label_rows,
        "label_rows": label_rows,
        "features_sha256": sha256_file(feature_path),
        "labels_sha256": sha256_file(label_path),
        "metric_availability_sha256": availability_hash,
        "extractor_forbidden_fields": [
            "target",
            "exit_status",
            "generated_patch",
            "eval_logs",
            "reward",
            "pass",
            "eval_details",
        ],
        "api_or_model_calls": 0,
        "api_credit_spend_usd": 0.0,
    }
    write_json(output / BINDING_FILE, receipt)
    return receipt


def _verify_prepared(prepared: Path) -> tuple[Path, Path, dict[str, Any]]:
    feature_path = prepared / FEATURE_FILE
    label_path = prepared / LABEL_FILE
    binding_path = prepared / BINDING_FILE
    availability_path = prepared / STATUS_FILE
    for path in (feature_path, label_path, binding_path, availability_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    if sha256_file(feature_path) != binding.get("features_sha256"):
        raise ValueError("prepared feature hash mismatch")
    if sha256_file(label_path) != binding.get("labels_sha256"):
        raise ValueError("prepared label hash mismatch")
    if sha256_file(availability_path) != binding.get("metric_availability_sha256"):
        raise ValueError("metric availability hash mismatch")
    return feature_path, label_path, binding


def _summary_markdown(results: dict[str, Any], receipt: dict[str, Any]) -> str:
    lines = [
        "# Coherence Dynamics Nebius Trajectory Audit",
        "",
        f"**Disposition:** `{results['overall_disposition']}`",
        "",
        f"Trajectories analyzed: **{receipt['public_dataset_rows_analyzed']:,}**",
        "",
        "No model API was called and no API credit was spent.",
        "",
        "## Held-out results",
        "",
        "| Prefix | Simple PR-AUC | Simple + CD PR-AUC | Delta | Disposition |",
        "|---:|---:|---:|---:|---|",
    ]
    for prefix, row in sorted(results["prefix_results"].items(), key=lambda item: float(item[0])):
        simple = row["families"]["simple"]["heldout"]["pr_auc"]
        extended = row["families"]["simple_cd"]["heldout"]["pr_auc"]
        lines.append(f"| {float(prefix):.2f} | {simple:.4f} | {extended:.4f} | {extended-simple:+.4f} | `{row['disposition']}` |")
    lines.extend(
        [
            "",
            "## Claim boundary",
            "",
            "This evaluates benchmark coding-task success from saved trajectory fractions. It does not validate human handoff correction prediction, universal Coherence Dynamics, Phi-star, VKD, or the Terrynce Curve.",
            "",
        ]
    )
    return "\n".join(lines)


def run(
    prepared: Path,
    output: Path,
    *,
    bootstrap_iterations: int,
    data_manifest: Path | None = None,
) -> dict[str, Any]:
    feature_path, label_path, binding = _verify_prepared(prepared)
    features = pd.read_csv(feature_path)
    labels = pd.read_csv(label_path)
    if len(features) != int(binding["feature_rows"]) or len(labels) != int(binding["label_rows"]):
        raise ValueError("prepared row count mismatch")
    if features["trajectory_id"].nunique() != len(labels):
        raise ValueError("feature/label trajectory cardinality mismatch")
    merged = features.merge(labels[["trajectory_id", "target"]], on="trajectory_id", how="inner", validate="many_to_one")
    if len(merged) != len(features):
        raise ValueError("one or more blind feature rows lack a sealed label")
    if set(int(value) for value in merged["target"].unique()) != {0, 1}:
        raise ValueError("both outcome classes are required")

    output.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {
        "schema": "coherence-dynamics.public-trajectory.calibration-audit.v2",
        "audit_id": "CD_PUBLIC_CODING_TRAJECTORY_CALIBRATION_001",
        "created_at_utc": _utc_now(),
        "claim_boundary": "Predicts independently evaluated coding-task success from saved observable trajectory fractions. It does not establish human handoff correction prediction or universal Coherence Dynamics.",
        "horizon_limitation": "Fractional prefixes require knowledge of final trajectory length and are retrospective normalized horizons, not deployable clock-time triggers.",
        "api_or_model_calls": 0,
        "api_credit_spend_usd": 0.0,
        "public_dataset_rows_analyzed": len(labels),
        "prefix_results": {},
    }
    for prefix in sorted(merged["prefix"].unique()):
        subset = merged[merged["prefix"] == prefix].reset_index(drop=True)
        results["prefix_results"][str(prefix)] = evaluate_prefix(subset, bootstrap_iterations=bootstrap_iterations)
    dispositions = [row["disposition"] for row in results["prefix_results"].values()]
    results["overall_disposition"] = (
        "CD_ADDS_HELDOUT_SIGNAL"
        if dispositions.count("CD_ADDS_HELDOUT_SIGNAL") >= 2
        else (
            "BASELINE_OUTPERFORMS_CD"
            if dispositions.count("BASELINE_OUTPERFORMS_CD") >= 2
            else (
                "BASELINE_EQUIVALENT"
                if dispositions.count("BASELINE_EQUIVALENT") >= 2
                else "NO_RELIABLE_SIGNAL"
            )
        )
    )
    if results["overall_disposition"] not in _ALLOWED_DISPOSITIONS:
        raise AssertionError("invalid overall disposition")
    result_path = output / "AUDIT_RESULT.json"
    result_hash = write_json(result_path, results)

    source_hashes = {
        name: sha256_file(ROOT / name)
        for name in (
            "AUDIT_PROTOCOL.json",
            "SOURCE_REGISTER.json",
            "FEATURE_SCHEMA.json",
            "LEAKAGE_POLICY.json",
            "RESULT_RULE.json",
            "requirements.txt",
        )
    }
    receipt = {
        "schema": "coherence-dynamics.public-trajectory.execution-receipt.v1",
        "audit_id": results["audit_id"],
        "created_at_utc": _utc_now(),
        "disposition": results["overall_disposition"],
        "public_dataset_rows_analyzed": len(labels),
        "feature_rows_analyzed": len(features),
        "bootstrap_iterations_requested": bootstrap_iterations,
        "source_hashes": source_hashes,
        "data_manifest_sha256": sha256_file(data_manifest) if data_manifest else binding.get("data_manifest_sha256"),
        "feature_label_binding_sha256": sha256_file(prepared / BINDING_FILE),
        "audit_result_sha256": result_hash,
        "model_api_calls": 0,
        "api_credit_spend_usd": 0.0,
        "real_assignments_created": 0,
        "scientific_claim": "bounded coding-trajectory benchmark result only",
    }
    write_json(output / "RUN_RECEIPT.json", receipt)
    (output / "EXECUTION_SUMMARY.md").write_text(_summary_markdown(results, receipt), encoding="utf-8")
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p_prepare = sub.add_parser("prepare")
    p_prepare.add_argument("--input", action="append", required=True)
    p_prepare.add_argument("--output", required=True)
    p_prepare.add_argument("--data-manifest")
    p_run = sub.add_parser("run")
    p_run.add_argument("--prepared", required=True)
    p_run.add_argument("--output", required=True)
    p_run.add_argument("--bootstrap-iterations", type=int, default=500)
    p_run.add_argument("--data-manifest")
    args = parser.parse_args()
    if args.command == "prepare":
        print(
            json.dumps(
                json_safe(prepare(
                    [Path(path) for path in args.input],
                    Path(args.output),
                    data_manifest=Path(args.data_manifest) if args.data_manifest else None,
                )),
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(
            json.dumps(
                json_safe(
                    run(
                        Path(args.prepared),
                        Path(args.output),
                        bootstrap_iterations=args.bootstrap_iterations,
                        data_manifest=Path(args.data_manifest) if args.data_manifest else None,
                    )
                ),
                indent=2,
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
