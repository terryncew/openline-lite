from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .split import development_folds

IDENTITY_COLUMNS = {"trajectory_id", "instance_id", "repository", "model_name", "prefix", "target"}
STATUS_SUFFIX = "_status"


def feature_columns(frame: pd.DataFrame, family: str) -> list[str]:
    numeric = [name for name in frame.columns if name not in IDENTITY_COLUMNS and not name.endswith(STATUS_SUFFIX) and pd.api.types.is_numeric_dtype(frame[name])]
    cd = [name for name in numeric if "_kappa_" in name or "_csd_" in name]
    length = [name for name in ("action_count", "observable_chars") if name in numeric]
    simple = [name for name in numeric if name not in cd]
    if family == "length":
        return length
    if family == "simple":
        return simple
    if family == "cd":
        return cd
    if family == "simple_cd":
        return simple + cd
    raise ValueError(f"unknown family: {family}")


def pipeline(columns: list[str], *, C: float) -> Pipeline:
    prep = ColumnTransformer(
        [("numeric", Pipeline([("impute", SimpleImputer(strategy="median", keep_empty_features=True)), ("scale", StandardScaler())]), columns)],
        remainder="drop",
    )
    model = LogisticRegression(C=C, class_weight="balanced", max_iter=3000, solver="liblinear", random_state=20260804)
    return Pipeline([("prep", prep), ("model", model)])


@dataclass
class FittedFamily:
    family: str
    columns: list[str]
    C: float
    model: Pipeline
    oof_predictions: np.ndarray


def fit_family(dev: pd.DataFrame, family: str) -> FittedFamily:
    columns = feature_columns(dev, family)
    if not columns:
        raise ValueError(f"no available features for {family}")
    best: tuple[float, float, np.ndarray] | None = None
    for C in (0.01, 0.1, 1.0, 10.0):
        predictions = np.full(len(dev), np.nan)
        for train_pos, val_pos in development_folds(dev):
            model = pipeline(columns, C=C)
            model.fit(dev.iloc[train_pos], dev.iloc[train_pos]["target"])
            predictions[val_pos] = model.predict_proba(dev.iloc[val_pos])[:, 1]
        score = average_precision_score(dev["target"], predictions)
        candidate = (float(score), -math.log10(C) ** 2, predictions)
        if best is None or candidate[:2] > best[:2]:
            best = candidate
            best_C = C
    assert best is not None
    final = pipeline(columns, C=best_C)
    final.fit(dev, dev["target"])
    return FittedFamily(family, columns, best_C, final, best[2])


def metrics(y: pd.Series, probability: np.ndarray) -> dict[str, float]:
    unique = set(int(value) for value in y)
    return {
        "roc_auc": float(roc_auc_score(y, probability)) if unique == {0, 1} else math.nan,
        "pr_auc": float(average_precision_score(y, probability)) if unique == {0, 1} else math.nan,
        "brier": float(brier_score_loss(y, probability)),
    }


def threshold_at_fpr(y: pd.Series, probability: np.ndarray, max_fpr: float = 0.20) -> float:
    candidates = sorted(set(float(value) for value in probability), reverse=True)
    best = 1.0
    best_recall = -1.0
    y_array = np.asarray(y, dtype=int)
    for threshold in candidates:
        pred = probability >= threshold
        negatives = y_array == 0
        positives = y_array == 1
        fpr = float(np.mean(pred[negatives])) if negatives.any() else 0.0
        recall = float(np.mean(pred[positives])) if positives.any() else 0.0
        if fpr <= max_fpr and recall > best_recall:
            best = threshold
            best_recall = recall
    return float(best)


def operating_metrics(y: pd.Series, probability: np.ndarray, threshold: float) -> dict[str, float | int]:
    y_array = np.asarray(y, dtype=int)
    pred = probability >= threshold
    positives = y_array == 1
    negatives = y_array == 0
    tp = int(np.sum(pred & positives)); fp = int(np.sum(pred & negatives))
    fn = int(np.sum((~pred) & positives)); tn = int(np.sum((~pred) & negatives))
    return {
        "threshold": threshold,
        "true_positive": tp, "false_positive": fp, "false_negative": fn, "true_negative": tn,
        "recall": tp / (tp + fn) if tp + fn else math.nan,
        "precision": tp / (tp + fp) if tp + fp else math.nan,
        "false_positive_rate": fp / (fp + tn) if fp + tn else math.nan,
        "review_rate": (tp + fp) / len(y_array) if len(y_array) else math.nan,
    }
