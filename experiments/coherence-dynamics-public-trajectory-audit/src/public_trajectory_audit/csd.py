from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np


def _acf1(values: Sequence[float]) -> float:
    array = np.asarray(values, dtype=float)
    if len(array) < 3:
        return math.nan
    left = array[:-1]
    right = array[1:]
    if np.std(left) == 0 or np.std(right) == 0:
        return 0.0
    return float(np.corrcoef(left, right)[0, 1])


def rolling_csd_summary(kappa: Sequence[int], window: int) -> dict[str, float]:
    """Summarize v1.2 CSD candidates: rolling variance and lag-1 ACF of kappa."""
    if window < 3:
        raise ValueError("window must be >= 3")
    if len(kappa) < window:
        return {
            f"csd_var_w{window}_last": math.nan,
            f"csd_var_w{window}_max": math.nan,
            f"csd_acf1_w{window}_last": math.nan,
            f"csd_acf1_w{window}_max": math.nan,
        }
    windows = [kappa[i - window : i] for i in range(window, len(kappa) + 1)]
    variances = [float(np.var(chunk, ddof=0)) for chunk in windows]
    acfs = [_acf1(chunk) for chunk in windows]
    finite_acfs = [value for value in acfs if math.isfinite(value)]
    return {
        f"csd_var_w{window}_last": variances[-1],
        f"csd_var_w{window}_max": max(variances),
        f"csd_acf1_w{window}_last": acfs[-1],
        f"csd_acf1_w{window}_max": max(finite_acfs) if finite_acfs else math.nan,
    }
