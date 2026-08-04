from __future__ import annotations

import math
from collections.abc import Sequence

MICROS = 1_000_000


def curvature_point_micros(x0: int, x1: int, x2: int) -> int:
    """Exact integer curvature kernel reused from Experiment 003.

    This is kernel reuse only. It is not the complete frozen Experiment 003
    mapper, which requires dependency-edge and structured state observations
    absent from the public Nebius trajectory schema.
    """
    numerator = abs(x2 - 2 * x1 + x0)
    dx = x1 - x0
    base = MICROS**2 + dx**2
    return (numerator * MICROS**3) // (base * math.isqrt(base))


def curvature_series_micros(signal_micros: Sequence[int]) -> list[int]:
    if len(signal_micros) < 3:
        return []
    return [
        curvature_point_micros(x0, x1, x2)
        for x0, x1, x2 in zip(signal_micros, signal_micros[1:], signal_micros[2:])
    ]


def to_micros(value: float) -> int:
    if not math.isfinite(value):
        raise ValueError("non-finite signal value")
    return max(0, min(MICROS, int(round(value * MICROS))))
