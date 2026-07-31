from __future__ import annotations

import hashlib
from math import isqrt
from typing import Any

from .common import SCALE, TrialError, canonical_json, validate_profile, validate_session


def _hash_obj(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def smooth_signal(signal: list[int], window: int) -> list[int]:
    return [
        sum(signal[max(0, i - window + 1) : i + 1]) // min(window, i + 1)
        for i in range(len(signal))
    ]


def curvature_point_micros(x0: int, x1: int, x2: int) -> int:
    numerator = abs(x2 - 2 * x1 + x0)
    dx = x1 - x0
    base = SCALE * SCALE + dx * dx
    return numerator * SCALE**3 // (base * isqrt(base))


def curvature_micros(signal: list[int], smoothing_window: int) -> int | None:
    if len(signal) < 3:
        return None
    smoothed = smooth_signal(signal, smoothing_window)
    return max(
        curvature_point_micros(smoothed[i - 1], smoothed[i], smoothed[i + 1])
        for i in range(1, len(smoothed) - 1)
    )


def epsilon_micros(
    signal: list[int], window: int, smoothing_window: int
) -> int | None:
    smoothed = smooth_signal(signal, smoothing_window)
    if len(smoothed) < window:
        return None
    values = []
    for end in range(window, len(smoothed) + 1):
        sample = smoothed[end - window : end]
        total = sum(sample)
        numerator = window * sum(v * v for v in sample) - total * total
        values.append(isqrt(max(0, numerator)) // window)
    return max(values)


def _groups(graph: dict[str, Any]) -> dict[str, set[str]]:
    return {
        name: {_hash_obj(item) for item in graph[name]}
        for name in ("claims", "evidence", "relations")
    }


def _set_drift(current: set[str], previous: set[str]) -> int:
    union = current | previous
    return 0 if not union else len(union - (current & previous)) * SCALE // len(union)


def graph_drift(
    current: dict[str, Any], previous: dict[str, Any], receiver_profile: dict[str, Any]
) -> tuple[int, dict[str, int]]:
    cg, pg = _groups(current), _groups(previous)
    vector = {
        "claim_micros": _set_drift(cg["claims"], pg["claims"]),
        "evidence_micros": _set_drift(cg["evidence"], pg["evidence"]),
        "relation_micros": _set_drift(cg["relations"], pg["relations"]),
    }
    weighted = (
        receiver_profile["dhol_claim_weight_micros"] * vector["claim_micros"] ** 2
        + receiver_profile["dhol_evidence_weight_micros"]
        * vector["evidence_micros"] ** 2
        + receiver_profile["dhol_relation_weight_micros"]
        * vector["relation_micros"] ** 2
    ) // SCALE
    return isqrt(weighted), vector


def compute_metrics(
    session: dict[str, Any], receiver_profile: dict[str, Any]
) -> dict[str, Any]:
    session = {k: v for k, v in session.items() if not k.startswith("_")}
    validate_session(session)
    validate_profile(receiver_profile)
    mi = session["measurement_input"]
    sig = mi["signal_points_micros"]
    p = receiver_profile
    kappa = curvature_micros(sig, p["smoothing_window"])
    epsilon = epsilon_micros(sig, p["epsilon_window"], p["smoothing_window"])
    dhol, vector = graph_drift(mi["current_graph"], mi["previous_graph"], p)
    phi = None
    vkd = None
    if kappa is not None and epsilon is not None:
        denom = (
            SCALE
            + p["alpha_k_micros"] * kappa // SCALE
            + p["alpha_e_micros"] * epsilon // SCALE
            + p["stability_delta_micros"]
        )
        phi = p["i_c_micros"] * SCALE // denom
        vkd = min(p["kappa_critical_micros"] - kappa, phi - p["phi_min_micros"])
    return {
        "kappa_micros": kappa,
        "epsilon_micros": epsilon,
        "delta_hol_micros": dhol,
        "phi_star_micros": phi,
        "vkd_micros": vkd,
        "drift_vector": vector,
    }


def transcript_features(session: dict[str, Any], terms: list[str]) -> dict[str, int]:
    session = {k: v for k, v in session.items() if not k.startswith("_")}
    validate_session(session)
    transcript = session["transcript"]
    length = sum(1 for e in transcript if e["role"] in {"user", "assistant"})
    tools = sum(1 for e in transcript if e["role"] == "tool")
    assistants = [e["text"] for e in transcript if e["role"] == "assistant"]
    last = assistants[-1].lower() if assistants else ""
    keyword = int(any(term.lower() in last for term in terms))
    return {
        "session_length": length,
        "tool_call_count": tools,
        "keyword_heuristic": keyword,
    }


def require_complete_primary(metrics: dict[str, Any]) -> None:
    if metrics.get("delta_hol_micros") is None:
        raise TrialError("primary delta_hol metric is unavailable")
