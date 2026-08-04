#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "NON_SCIENTIFIC_TEST_ONLY"
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def fake_trajectory(target: bool, seed: int) -> list[dict[str, object]]:
    events: list[dict[str, object]] = [
        {"role": "system", "system_prompt": "synthetic mechanics fixture", "text": None},
        {"role": "ai", "text": f"search_dir issue_{seed} src"},
        {"role": "user", "text": "src/a.py"},
        {"role": "ai", "text": "open src/a.py 1"},
        {"role": "user", "text": f"def f_{seed}(): pass"},
        {"role": "ai", "text": "edit src/a.py 1:1"},
        {"role": "user", "text": "Done!"},
        {"role": "ai", "text": "pytest -q"},
        {"role": "user", "text": "1 passed" if target else "1 failed"},
    ]
    if not target:
        events.extend(
            [
                {"role": "ai", "text": "edit src/a.py 1:1"},
                {"role": "user", "text": "ERROR still failing"},
                {"role": "ai", "text": "pytest -q"},
                {"role": "user", "text": "1 failed"},
            ]
        )
    events.append({"role": "ai", "text": "submit"})
    return events


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, env=ENV, text=True, capture_output=True)


def main() -> None:
    shutil.rmtree(OUT, ignore_errors=True)
    (OUT / "data").mkdir(parents=True)
    source = OUT / "data" / "synthetic_nebius.jsonl"
    rows = []
    for repo_index in range(10):
        for task_index in range(6):
            target = task_index % 2 == 0
            seed = repo_index * 100 + task_index
            rows.append(
                {
                    "instance_id": f"owner{repo_index}__repo{repo_index}-{seed}",
                    "model_name": f"fake-model-{task_index % 2}",
                    "target": target,
                    "trajectory": fake_trajectory(target, seed),
                    "exit_status": "SECRET",
                    "generated_patch": "SECRET_PATCH",
                    "eval_logs": "SECRET_EVAL",
                }
            )
    source.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    manifest = {
        "schema": "coherence-dynamics.public-data-acquisition.synthetic.v1",
        "files": {f"data/{source.name}": {"bytes": source.stat().st_size, "sha256": sha256(source)}},
        "model_api_calls": 0,
        "scientific_result": "NON_SCIENTIFIC_TEST_ONLY",
    }
    manifest_path = OUT / "data" / "DATA_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    commands = [
        [sys.executable, "-m", "unittest", "discover", "-s", str(ROOT / "tests"), "-v"],
        [
            sys.executable,
            "scripts/run_audit.py",
            "prepare",
            "--input",
            str(source),
            "--data-manifest",
            str(manifest_path),
            "--output",
            str(OUT / "prepared"),
        ],
        [
            sys.executable,
            "scripts/run_audit.py",
            "run",
            "--prepared",
            str(OUT / "prepared"),
            "--data-manifest",
            str(manifest_path),
            "--output",
            str(OUT / "result"),
            "--bootstrap-iterations",
            "20",
        ],
        [
            sys.executable,
            "scripts/verify_package.py",
            "--result-dir",
            str(OUT / "result"),
            "--prepared-dir",
            str(OUT / "prepared"),
            "--data-manifest",
            str(manifest_path),
        ],
    ]
    reports = []
    returncode = 0
    for command in commands:
        completed = run_command(command)
        reports.append(
            {
                "command": command,
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
        )
        print(completed.stdout, end="")
        print(completed.stderr, end="", file=sys.stderr)
        if completed.returncode != 0:
            returncode = completed.returncode
            break
    report = {
        "schema": "coherence-dynamics.public-trajectory.selftest.v2",
        "commands": reports,
        "returncode": returncode,
        "network_allowed": False,
        "model_api_calls": 0,
        "api_credit_spend_usd": 0.0,
        "scientific_result": "NON_SCIENTIFIC_TEST_ONLY",
    }
    (OUT / "SELFTEST_REPORT.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    raise SystemExit(returncode)


if __name__ == "__main__":
    main()
