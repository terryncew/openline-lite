from __future__ import annotations

import hashlib
import json
import py_compile
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "build" / "RELEASE_VERIFICATION.json"


def run(cmd: list[str]) -> tuple[bool, str]:
    try:
        process = subprocess.run(
            cmd,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"subprocess failure: {exc}"
    text = (process.stdout + "\n" + process.stderr).strip()
    return process.returncode == 0, text[-16000:]


def add(checks: list[dict], name: str, ok: bool, detail: str) -> None:
    checks.append(
        {"name": name, "status": "pass" if ok else "fail", "detail": detail}
    )


def main() -> int:
    checks: list[dict] = []

    try:
        paths = (
            sorted((ROOT / "calibration_trial").glob("*.py"))
            + sorted((ROOT / "examples").glob("*.py"))
            + sorted((ROOT / "tests").glob("*.py"))
            + [ROOT / "scripts" / "release_check.py"]
        )
        for path in paths:
            py_compile.compile(str(path), doraise=True)
        add(
            checks,
            "Python compile",
            True,
            "candidate, independent verifier, fixture, tests, and release gate compile",
        )
    except Exception as exc:
        add(checks, "Python compile", False, str(exc))

    try:
        protocol = json.loads((ROOT / "protocol.yaml").read_text(encoding="utf-8"))
        measurement = protocol["measurement_contract"]
        inference = protocol["primary_inference"]
        outcome = protocol["outcome"]
        boundary_ok = (
            protocol["schema"] == "openline.calibration-trial.protocol.v2"
            and protocol["trial_id"] == "olp-handoff-calibration-003"
            and protocol["split"]["type"] == "prospective_temporal"
            and protocol["split"]["random_split_forbidden"] is True
            and protocol["sample"]["calibration_n"] == 20
            and protocol["sample"]["test_n"] == 20
            and protocol["sample"]["total_n"] == 40
            and measurement["profile_authority"] == "receiver"
            and measurement["producer_profile_fields_forbidden"] is True
            and sum(
                measurement["receiver_profile"][key]
                for key in (
                    "dhol_claim_weight_micros",
                    "dhol_evidence_weight_micros",
                    "dhol_relation_weight_micros",
                )
            )
            == 1_000_000
            and "all 20" in outcome["blackout"]
            and inference["alpha_micros"] == 50_000
            and inference["confidence_micros"] == 950_000
            and set(protocol["comparators"])
            == {"always_safe", "session_length", "tool_call_count", "keyword_heuristic"}
        )
        add(
            checks,
            "Preregistered scientific boundary",
            boundary_ok,
            "fixed 20+20 temporal split, receiver-owned metric profile, all-predictions label blackout, four baselines, exact alpha=0.05 inference, and 95% interval are frozen",
        )
    except Exception as exc:
        protocol = {}
        add(checks, "Preregistered scientific boundary", False, str(exc))

    try:
        prereg = json.loads((ROOT / "PREREGISTRATION.json").read_text(encoding="utf-8"))
        prereg_ok = (
            prereg.get("schema") == "openline.calibration-trial.preregistration.v2"
            and prereg.get("protocol_sha256")
            == hashlib.sha256((ROOT / "protocol.yaml").read_bytes()).hexdigest()
            and prereg.get("trial_id") == protocol.get("trial_id")
            and prereg.get("supersedes_trial_id") == "olp-handoff-calibration-002"
            and prereg.get("claim")
            == "Protocol bytes frozen before prospective held-out scoring."
        )
        add(
            checks,
            "Preregistration receipt",
            prereg_ok,
            "trial-003 binds exact protocol bytes and explicitly supersedes trial-002; local timestamp is not an external time authority",
        )
    except Exception as exc:
        add(checks, "Preregistration receipt", False, str(exc))

    ok, text = run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
    add(checks, "Deterministic unit/adversarial tests", ok, text)

    endpoint_ok, endpoint_text = run(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            "test_trial.py",
            "-k",
            "all_one_class",
            "-v",
        ]
    )
    add(
        checks,
        "Insufficient-sample endpoint regression",
        endpoint_ok,
        endpoint_text,
    )

    temporal_ok, temporal_text = run(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            "test_trial.py",
            "-k",
            "calibration_continuation_must_start_after_handoff",
            "-v",
        ]
    )
    add(
        checks,
        "Calibration temporal-causality regression",
        temporal_ok,
        temporal_text,
    )

    report_isolation_ok = (
        REPORT.parent == ROOT / "build"
        and REPORT.name == "RELEASE_VERIFICATION.json"
        and not (ROOT / "RELEASE_VERIFICATION.json").exists()
    )
    add(
        checks,
        "Runtime report isolation",
        report_isolation_ok,
        "release verification is generated under ignored build/ and is not a sealed source file",
    )

    verifier = (ROOT / "calibration_trial" / "independent_verify.py").read_text(
        encoding="utf-8"
    )
    independent = "from ." not in verifier and "import calibration_trial" not in verifier
    add(
        checks,
        "Independent verifier import boundary",
        independent,
        "independent verifier imports no candidate calibration_trial modules",
    )

    tmp = Path(tempfile.mkdtemp(prefix="olp-calibration-release-"))
    try:
        fixture = tmp / "fixture"
        ok_fixture, fixture_text = run(
            [
                sys.executable,
                "-m",
                "examples.synthetic_fixture",
                "--root",
                str(fixture),
                "--protocol",
                str(ROOT / "protocol.yaml"),
            ]
        )
        add(
            checks,
            "Mechanism-only synthetic trial",
            ok_fixture,
            fixture_text,
        )

        verify_cmd = [
            sys.executable,
            "-m",
            "calibration_trial.independent_verify",
            str(ROOT / "protocol.yaml"),
            str(fixture / "build" / "freeze.json"),
            "--calibration-sessions",
            str(fixture / "data" / "calibration"),
            "--calibration-continuations",
            str(fixture / "continuations" / "calibration"),
            "--calibration-outcomes",
            str(fixture / "labels" / "calibration.jsonl"),
            "--test-sessions",
            str(fixture / "data" / "test"),
            "--eligibility-ledger",
            str(fixture / "build" / "eligibility.jsonl"),
            "--test-continuations",
            str(fixture / "continuations" / "test"),
            "--predictions",
            str(fixture / "build" / "predictions.jsonl"),
            "--outcome-unlock",
            str(fixture / "build" / "outcome-unlock.json"),
            "--test-outcomes",
            str(fixture / "labels" / "test.jsonl"),
            "--evaluation",
            str(fixture / "build" / "evaluation.json"),
        ]
        if ok_fixture:
            ok_verify, verify_text = run(verify_cmd)
        else:
            ok_verify, verify_text = False, "synthetic trial did not run"
        add(
            checks,
            "Independent end-to-end recomputation",
            ok_verify,
            verify_text,
        )

        if ok_verify:
            first = json.loads(
                (fixture / "data" / "test" / "test-001.json").read_text(
                    encoding="utf-8"
                )
            )
            first["session_id"] = "omitted-earlier"
            first["handoff_at_utc"] = "2026-01-25T12:00:00Z"
            attack = fixture / "data" / "test" / "omitted-earlier.json"
            attack.write_text(
                json.dumps(first, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            attack_ok, attack_text = run(verify_cmd)
            add(
                checks,
                "Omitted earlier handoff fails closed",
                not attack_ok,
                attack_text,
            )
            attack.unlink()

            outcome_rows = [
                json.loads(line)
                for line in (fixture / "labels" / "test.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            unlock = json.loads(
                (fixture / "build" / "outcome-unlock.json").read_text(
                    encoding="utf-8"
                )
            )
            unlock_dt = datetime.fromisoformat(
                unlock["generated_at_utc"].replace("Z", "+00:00")
            )
            outcome_rows[0]["labeled_at_utc"] = (
                unlock_dt - timedelta(seconds=1)
            ).astimezone(timezone.utc).isoformat(timespec="seconds").replace(
                "+00:00", "Z"
            )
            original_outcomes = fixture / "labels" / "test.jsonl"
            backup = fixture / "labels" / "test-original.jsonl"
            shutil.copy2(original_outcomes, backup)
            original_outcomes.write_text(
                "".join(
                    json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
                    for row in outcome_rows
                ),
                encoding="utf-8",
            )
            blackout_ok, blackout_text = run(verify_cmd)
            add(
                checks,
                "Pre-unlock label tamper fails closed",
                not blackout_ok,
                blackout_text,
            )
            shutil.move(str(backup), str(original_outcomes))

            evaluation = json.loads(
                (fixture / "build" / "evaluation.json").read_text(encoding="utf-8")
            )
            inference_ok = (
                evaluation.get("primary_gate", {}).get(
                    "exact_randomization_p_le_alpha_against_all"
                )
                is True
                and set(evaluation.get("inference", {}))
                == set(protocol.get("comparators", {}))
                and all(
                    result["bootstrap_interval"]["confidence_micros"] == 950_000
                    and "p_value_numerator" in result["exact_randomization"]
                    and "p_value_denominator" in result["exact_randomization"]
                    for result in evaluation.get("inference", {}).values()
                )
            )
            add(
                checks,
                "Preregistered inference emitted",
                inference_ok,
                "evaluation reports exact paired p-values and 95% paired effect intervals for every comparator",
            )
        else:
            add(
                checks,
                "Omitted earlier handoff fails closed",
                False,
                "independent baseline did not pass",
            )
            add(
                checks,
                "Pre-unlock label tamper fails closed",
                False,
                "independent baseline did not pass",
            )
            add(
                checks,
                "Preregistered inference emitted",
                False,
                "independent baseline did not pass",
            )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    ok, text = run([sys.executable, "-m", "calibration_trial", "--help"])
    add(checks, "CLI smoke", ok, text)

    repo_ci = ROOT.parents[1] / ".github" / "workflows" / "ci.yml"
    if repo_ci.exists():
        ci_text = repo_ci.read_text(encoding="utf-8")
        ci_ok = (
            "experiments/calibration-trial" in ci_text
            and "python scripts/release_check.py" in ci_text
        )
        add(
            checks,
            "Repository CI integration",
            ci_ok,
            "root CI runs Calibration Trial tests and the release gate",
        )
    else:
        add(
            checks,
            "Repository CI integration",
            False,
            "root .github/workflows/ci.yml not present in candidate",
        )

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    boundary = (ROOT / "CLAIM_BOUNDARY.md").read_text(encoding="utf-8")
    claim_ok = (
        "does **not** establish" in readme
        and "mechanism-only" in boundary.lower()
        and "psychologically blinded" in boundary.lower()
        and "universal threshold" in boundary.lower()
    )
    add(
        checks,
        "Claim boundary",
        claim_ok,
        "predictive validity, extractor validity, external timestamp authority, psychological blinding, and universal claims are withheld",
    )

    protocol_hash = hashlib.sha256((ROOT / "protocol.yaml").read_bytes()).hexdigest()
    disposition = "clear" if all(check["status"] == "pass" for check in checks) else "hold"
    report = {
        "schema": "openline.calibration-trial.release-verification.v2",
        "generated_at_utc": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "disposition": disposition,
        "checks": checks,
        "protocol_sha256": protocol_hash,
        "trial_id": protocol.get("trial_id"),
        "claim": (
            "This build freezes and verifies the prospective calibration procedure; "
            "it does not report a real predictive result."
        ),
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if disposition == "clear" else 1


if __name__ == "__main__":
    raise SystemExit(main())
