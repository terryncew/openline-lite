from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from public_trajectory_audit.data_io import iter_rows
from public_trajectory_audit.features import extract_prefix
from public_trajectory_audit.nebius import blind_record, label_row, sanitize_row

from .adapter import (
    KNOWN_SOURCES,
    iter_external_rows,
    parse_meta,
    record_and_label,
    source_admissibility,
)
from .canonical import sha256_file, write_json


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write(
    rows: Iterable[tuple[dict[str, Any], dict[str, Any]]],
    output: Path,
    receipt_extra: dict[str, Any],
) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    features_path = output / "features_blind_075.csv"
    labels_path = output / "labels_sealed.csv"
    features_temp = features_path.with_suffix(".tmp")
    labels_temp = labels_path.with_suffix(".tmp")
    feature_writer = label_writer = None
    count = 0
    seen: set[str] = set()
    cohorts: dict[str, int] = {}
    with features_temp.open("w", newline="", encoding="utf-8") as feature_handle, labels_temp.open(
        "w", newline="", encoding="utf-8"
    ) as label_handle:
        for feature, label in rows:
            trajectory_id = label["trajectory_id"]
            if trajectory_id in seen:
                raise ValueError("duplicate trajectory id")
            seen.add(trajectory_id)
            if feature_writer is None:
                feature_writer = csv.DictWriter(feature_handle, fieldnames=list(feature))
                feature_writer.writeheader()
            if label_writer is None:
                label_writer = csv.DictWriter(label_handle, fieldnames=list(label))
                label_writer.writeheader()
            feature_writer.writerow(feature)
            label_writer.writerow(label)
            count += 1
            cohort = label.get("source_dataset")
            if cohort:
                cohorts[cohort] = cohorts.get(cohort, 0) + 1
    if not count:
        raise ValueError("no rows prepared")
    features_temp.replace(features_path)
    labels_temp.replace(labels_path)
    receipt = {
        "schema": "coherence-dynamics.external-replication.prepared-binding.v2",
        "created_at_utc": now(),
        "rows": count,
        "horizon": 0.75,
        "features_sha256": sha256_file(features_path),
        "labels_sha256": sha256_file(labels_path),
        "cohort_rows": cohorts,
        "api_or_model_calls": 0,
        "api_credit_spend_usd": 0.0,
        **receipt_extra,
    }
    write_json(output / "FEATURE_LABEL_BINDING.json", receipt)
    return receipt


def prepare_source(paths: list[Path], output: Path, expected_hashes: dict[str, str]) -> dict[str, Any]:
    actual = {}
    for path in paths:
        key = f"data/{path.name}"
        digest = sha256_file(path)
        actual[key] = digest
        if expected_hashes.get(key) != digest:
            raise ValueError(f"source shard hash mismatch: {key}")

    def rows():
        for path in paths:
            for raw in iter_rows(path):
                record = blind_record(sanitize_row(raw))
                yield extract_prefix(record, 0.75), label_row(raw)

    return _write(
        rows(),
        output,
        {"dataset_role": "source_profile_recovery", "source_hashes": actual},
    )


def prepare_external(path: Path, output: Path, external_config: dict[str, Any]) -> dict[str, Any]:
    source_hash = sha256_file(path)
    expected_source_rows = external_config["expected_source_rows"]
    expected_included = external_config["included_sources"]
    observed_source_rows = {source: 0 for source in KNOWN_SOURCES}
    admissibility_rows: dict[str, int] = {}
    missing_recorded_model: dict[str, int] = {}
    missing_resolved: dict[str, int] = {}
    total_rows = 0

    def rows():
        nonlocal total_rows
        for raw in iter_external_rows(path):
            total_rows += 1
            source = raw.get("source_dataset")
            if source not in observed_source_rows:
                raise ValueError(f"unexpected external source: {source!r}")
            observed_source_rows[source] += 1
            status = source_admissibility(raw)
            admissibility_rows[status] = admissibility_rows.get(status, 0) + 1
            if raw.get("recorded_model") in (None, ""):
                missing_recorded_model[source] = missing_recorded_model.get(source, 0) + 1
            try:
                meta = parse_meta(raw.get("ground_truth_meta_json"))
            except Exception:
                meta = {}
            if not isinstance(meta.get("resolved"), (bool, int)):
                missing_resolved[source] = missing_resolved.get(source, 0) + 1
            if status != "INCLUDED_LABEL_COMPLETE":
                continue
            record, label = record_and_label(raw)
            yield extract_prefix(record, 0.75), label

    receipt = _write(
        rows(),
        output,
        {
            "dataset_role": "external_replication_labeled_only",
            "source_file_sha256": source_hash,
            "excluded_source_reasons": external_config["excluded_source_reasons"],
        },
    )

    if total_rows != external_config["expected_total_rows"]:
        raise ValueError(
            f"external total row mismatch: {total_rows} != {external_config['expected_total_rows']}"
        )
    if observed_source_rows != expected_source_rows:
        raise ValueError(
            f"external source row mismatch: {observed_source_rows} != {expected_source_rows}"
        )
    if receipt["cohort_rows"] != expected_included:
        raise ValueError(
            f"included cohort count mismatch: {receipt['cohort_rows']} != {expected_included}"
        )

    schema_audit = {
        "schema": "coherence-dynamics.external-replication.external-schema-audit.v1",
        "created_at_utc": now(),
        "source_file_sha256": source_hash,
        "total_rows": total_rows,
        "source_rows": observed_source_rows,
        "admissibility_rows": admissibility_rows,
        "missing_recorded_model_rows": missing_recorded_model,
        "missing_resolved_rows": missing_resolved,
        "included_sources": expected_included,
        "excluded_source_reasons": external_config["excluded_source_reasons"],
        "outcome_values_used_to_select_protocol_repair": False,
        "external_model_predictions_created_during_schema_audit": False,
    }
    write_json(output / "EXTERNAL_SCHEMA_AUDIT.json", schema_audit)
    receipt["external_schema_audit_sha256"] = sha256_file(output / "EXTERNAL_SCHEMA_AUDIT.json")
    write_json(output / "FEATURE_LABEL_BINDING.json", receipt)
    return receipt
