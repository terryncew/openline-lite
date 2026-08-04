from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from public_trajectory_audit.modeling import metrics, pipeline
from public_trajectory_audit.split import repository_holdout

from .canonical import sha256_file, write_json


def serialize_pipeline(fitted_model, family: str, columns: list[str], C: float, threshold: float) -> dict[str, Any]:
    numeric = fitted_model.named_steps["prep"].named_transformers_["numeric"]
    imputer = numeric.named_steps["impute"]
    scaler = numeric.named_steps["scale"]
    model = fitted_model.named_steps["model"]
    return {
        "family": family,
        "features": columns,
        "selected_C": float(C),
        "threshold": float(threshold),
        "imputer_statistics": [float(x) for x in imputer.statistics_],
        "scaler_mean": [float(x) for x in scaler.mean_],
        "scaler_scale": [float(x) for x in scaler.scale_],
        "coefficients": [float(x) for x in model.coef_[0]],
        "intercept": float(model.intercept_[0]),
        "class_order": [int(x) for x in model.classes_],
        "solver": "liblinear",
        "class_weight": "balanced",
        "random_state": 20260804,
    }


def apply_profile(frame: pd.DataFrame, profile: dict[str, Any]) -> np.ndarray:
    columns = profile["features"]
    missing = [column for column in columns if column not in frame]
    if missing:
        raise ValueError(f"external features missing frozen columns: {missing}")
    values = frame[columns].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    statistics = np.asarray(profile["imputer_statistics"], dtype=float)
    bad = ~np.isfinite(values)
    if bad.any():
        values[bad] = np.take(statistics, np.where(bad)[1])
    mean = np.asarray(profile["scaler_mean"], dtype=float)
    scale = np.asarray(profile["scaler_scale"], dtype=float)
    standardized = (values - mean) / scale
    logit = standardized @ np.asarray(profile["coefficients"], dtype=float) + float(profile["intercept"])
    logit = np.clip(logit, -709, 709)
    return 1.0 / (1.0 + np.exp(-logit))


def recover_source_profile(
    features_path: Path,
    labels_path: Path,
    lock_path: Path,
    recovery_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    lock = json.loads(lock_path.read_text())
    recovery = json.loads(recovery_path.read_text())
    features = pd.read_csv(features_path)
    labels = pd.read_csv(labels_path)
    frame = features.merge(labels[["trajectory_id", "target"]], on="trajectory_id", validate="one_to_one")
    if len(frame) != lock["source_dataset"]["expected_rows"]:
        raise ValueError("source row count differs from frozen audit")

    train_index, test_index = repository_holdout(
        frame,
        random_state=lock["split"]["repository_holdout_random_state"],
        test_size=lock["split"]["test_size"],
    )
    development = frame.loc[train_index].reset_index(drop=True)
    holdout = frame.loc[test_index].reset_index(drop=True)
    max_delta = float(recovery["source_metric_sanity"]["max_absolute_delta_each_metric"])

    result: dict[str, Any] = {
        "schema": "coherence-dynamics.external-replication.recovered-profile.v2",
        "profile_kind": "SOURCE_RECOVERED_AND_SEALED_BEFORE_EXTERNAL_ACQUISITION",
        "replication_id": recovery["replication_id"],
        "source_audit_result_sha256": lock["source_audit_result_sha256"],
        "source_profile_lock_sha256": sha256_file(lock_path),
        "source_profile_recovery_sha256": sha256_file(recovery_path),
        "horizon": 0.75,
        "development_rows": len(development),
        "source_holdout_rows": len(holdout),
        "external_dataset_acquired_at_freeze": False,
        "external_rows_scored_at_freeze": 0,
        "families": {},
    }

    for family in ("simple", "simple_cd"):
        expected = lock["families"][family]
        columns = list(expected["features"])
        if any(column not in development.columns for column in columns):
            missing = [column for column in columns if column not in development.columns]
            raise ValueError(f"{family} source features missing: {missing}")
        C = float(expected["selected_C"])
        fitted = pipeline(columns, C=C)
        fitted.fit(development, development["target"])
        probability = fitted.predict_proba(holdout)[:, 1]
        observed = metrics(holdout["target"], probability)
        deltas = {
            metric: float(observed[metric] - expected["source_heldout"][metric])
            for metric in expected["source_heldout"]
        }
        material = {metric: delta for metric, delta in deltas.items() if abs(delta) > max_delta}
        if material:
            raise ValueError(f"{family} material source pipeline drift: {material}")
        serialized = serialize_pipeline(
            fitted,
            family=family,
            columns=columns,
            C=C,
            threshold=float(expected["source_threshold"]),
        )
        serialized["historical_source_metrics"] = expected["source_heldout"]
        serialized["recovered_source_metrics"] = observed
        serialized["historical_metric_deltas"] = deltas
        serialized["metric_identity_status"] = "NUMERICALLY_EQUIVALENT_NOT_BITWISE_MODEL_IDENTITY"
        result["families"][family] = serialized

    profile_sha256 = write_json(output_path, result)
    receipt = {
        "schema": "coherence-dynamics.external-replication.source-recovery-receipt.v1",
        "replication_id": recovery["replication_id"],
        "status": "SOURCE_PROFILE_RECOVERED_AND_SEALED",
        "profile_sha256": profile_sha256,
        "source_features_sha256": sha256_file(features_path),
        "source_labels_sha256": sha256_file(labels_path),
        "source_profile_lock_sha256": sha256_file(lock_path),
        "source_profile_recovery_sha256": sha256_file(recovery_path),
        "external_dataset_acquired": False,
        "external_rows_scored": 0,
    }
    write_json(output_path.with_name("SOURCE_PROFILE_RECOVERY_RECEIPT.json"), receipt)
    return result
