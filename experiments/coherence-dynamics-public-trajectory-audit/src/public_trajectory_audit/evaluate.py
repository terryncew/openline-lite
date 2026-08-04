from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score

from .modeling import fit_family, metrics, operating_metrics, threshold_at_fpr
from .split import repository_holdout


def _group_bootstrap_delta(frame: pd.DataFrame, base: np.ndarray, extended: np.ndarray, *, iterations: int = 500, seed: int = 20260804) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    groups = frame["instance_id"].drop_duplicates().to_numpy()
    positions = {group: np.flatnonzero(frame["instance_id"].to_numpy() == group) for group in groups}
    values: list[float] = []
    y_all = frame["target"].to_numpy()
    for _ in range(iterations):
        sampled = rng.choice(groups, size=len(groups), replace=True)
        index = np.concatenate([positions[group] for group in sampled])
        y = y_all[index]
        if len(np.unique(y)) < 2:
            continue
        values.append(float(average_precision_score(y, extended[index]) - average_precision_score(y, base[index])))
    if not values:
        return {"iterations": 0, "lower_95": math.nan, "median": math.nan, "upper_95": math.nan}
    return {
        "iterations": len(values),
        "lower_95": float(np.quantile(values, 0.025)),
        "median": float(np.quantile(values, 0.5)),
        "upper_95": float(np.quantile(values, 0.975)),
    }


def disposition(base_metrics: dict[str, float], extended_metrics: dict[str, float], interval: dict[str, float]) -> str:
    delta = extended_metrics["pr_auc"] - base_metrics["pr_auc"]
    roc_delta = extended_metrics["roc_auc"] - base_metrics["roc_auc"]
    if delta > 0.02 and interval["lower_95"] > 0 and roc_delta >= -0.005:
        return "CD_ADDS_HELDOUT_SIGNAL"
    if delta < -0.01 and interval["upper_95"] < 0:
        return "BASELINE_OUTPERFORMS_CD"
    if abs(delta) <= 0.01 and interval["lower_95"] <= 0 <= interval["upper_95"]:
        return "BASELINE_EQUIVALENT"
    return "NO_RELIABLE_SIGNAL"


def evaluate_prefix(frame: pd.DataFrame, *, bootstrap_iterations: int = 500) -> dict[str, Any]:
    train_index, test_index = repository_holdout(frame)
    dev = frame.loc[train_index].reset_index(drop=True)
    test = frame.loc[test_index].reset_index(drop=True)
    if set(dev["repository"]) & set(test["repository"]):
        raise AssertionError("repository leakage")
    fitted = {family: fit_family(dev, family) for family in ("length", "simple", "cd", "simple_cd")}
    result: dict[str, Any] = {
        "development_rows": len(dev), "heldout_rows": len(test),
        "development_repositories": sorted(dev["repository"].unique()),
        "heldout_repositories": sorted(test["repository"].unique()),
        "families": {},
    }
    heldout_predictions: dict[str, np.ndarray] = {}
    for family, fitted_family in fitted.items():
        probability = fitted_family.model.predict_proba(test)[:, 1]
        heldout_predictions[family] = probability
        threshold = threshold_at_fpr(dev["target"], fitted_family.oof_predictions, max_fpr=0.20)
        result["families"][family] = {
            "feature_count": len(fitted_family.columns),
            "features": fitted_family.columns,
            "selected_C": fitted_family.C,
            "development_oof": metrics(dev["target"], fitted_family.oof_predictions),
            "heldout": metrics(test["target"], probability),
            "heldout_operating_point": operating_metrics(test["target"], probability, threshold),
        }
    interval = _group_bootstrap_delta(test, heldout_predictions["simple"], heldout_predictions["simple_cd"], iterations=bootstrap_iterations)
    base = result["families"]["simple"]["heldout"]
    extended = result["families"]["simple_cd"]["heldout"]
    result["simple_plus_cd_pr_auc_delta"] = extended["pr_auc"] - base["pr_auc"]
    result["simple_plus_cd_group_bootstrap"] = interval
    result["disposition"] = disposition(base, extended, interval)
    return result
