from __future__ import annotations

import pandas as pd
from sklearn.model_selection import GroupShuffleSplit, StratifiedGroupKFold


def repository_holdout(frame: pd.DataFrame, *, random_state: int = 20260804, test_size: float = 0.20) -> tuple[pd.Index, pd.Index]:
    splitter = GroupShuffleSplit(n_splits=1, test_size=test_size, random_state=random_state)
    train, test = next(splitter.split(frame, y=frame["target"], groups=frame["repository"]))
    return frame.index[train], frame.index[test]


def development_folds(frame: pd.DataFrame, *, random_state: int = 20260804, n_splits: int = 5):
    positive = int(frame["target"].sum())
    negative = len(frame) - positive
    splits = min(n_splits, positive, negative, frame["instance_id"].nunique())
    if splits < 2:
        raise ValueError("insufficient class/group support for development cross-validation")
    return StratifiedGroupKFold(n_splits=splits, shuffle=True, random_state=random_state).split(
        frame,
        y=frame["target"],
        groups=frame["instance_id"],
    )
