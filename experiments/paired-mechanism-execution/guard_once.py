from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

LOCK_ARTIFACT = "olp-30pair-assignment-lock"
ASSIGN_JOB_NAME = "assign-once"


def api_json(url: str, token: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def prior_assignment_evidence(repo: str, token: str, current_run_id: str) -> list[dict]:
    evidence = []

    # Durable evidence path 1: a surviving assignment-lock artifact.
    page = 1
    while True:
        obj = api_json(f"https://api.github.com/repos/{repo}/actions/artifacts?per_page=100&page={page}", token)
        arts = obj.get("artifacts", [])
        for a in arts:
            if a.get("name") == LOCK_ARTIFACT and not a.get("expired", False):
                evidence.append({
                    "kind": "assignment_lock_artifact",
                    "artifact_id": a.get("id"),
                    "workflow_run_id": (a.get("workflow_run") or {}).get("id"),
                })
        if len(arts) < 100:
            break
        page += 1

    # Durable evidence path 2: prior exact-tag push workflow runs in which assign-once was not skipped.
    # This closes the edge case where assignment was generated but artifact persistence failed.
    page = 1
    while True:
        obj = api_json(f"https://api.github.com/repos/{repo}/actions/runs?event=push&per_page=100&page={page}", token)
        runs = obj.get("workflow_runs", [])
        for run in runs:
            rid = str(run.get("id", ""))
            if not rid or rid == current_run_id:
                continue
            path = str(run.get("path") or "")
            name = str(run.get("name") or "")
            if not (path.endswith("olp-30pair-execution.yml") or name in {"OLP 30-pair real execution — exact tag only", "OLP 30-pair real execution — manual only"}):
                continue
            jobs = api_json(f"https://api.github.com/repos/{repo}/actions/runs/{rid}/jobs?per_page=100", token).get("jobs", [])
            for job in jobs:
                if job.get("name") != ASSIGN_JOB_NAME:
                    continue
                conclusion = job.get("conclusion")
                if conclusion != "skipped":
                    evidence.append({
                        "kind": "prior_assign_job_attempt",
                        "workflow_run_id": run.get("id"),
                        "job_id": job.get("id"),
                        "job_status": job.get("status"),
                        "job_conclusion": conclusion,
                    })
        if len(runs) < 100:
            break
        page += 1
    return evidence



def write_blocked(reason: str, detail=None):
    out = Path(__file__).resolve().parent / "build" / "EXECUTION_BLOCKED.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    obj = {
        "schema": "openline.paired-mechanism-benchmark.execution-blocked.v1",
        "experiment_id": "olp-core21-paired-mechanism-001",
        "benchmark_revision": "RESEALED_AFTER_SCOPE_REPAIR",
        "status": "EXECUTION_BLOCKED",
        "failed_stage": "assignment_once_guard",
        "failure_reason": reason,
        "benchmark_model_calls": 0,
        "real_condition_assignments": 0,
        "unblinded": False,
    }
    if detail is not None:
        obj["detail"] = detail
    out.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main():
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    current_run_id = os.environ.get("GITHUB_RUN_ID", "")
    if not repo or not token or not current_run_id:
        write_blocked("assignment guard missing GitHub repository/token/run id")
        print("assignment guard missing GitHub repository/token/run id", file=sys.stderr)
        raise SystemExit(1)
    try:
        found = prior_assignment_evidence(repo, token, current_run_id)
    except Exception as e:
        write_blocked("assignment guard API check failed", {"error": f"{type(e).__name__}:{e}"})
        raise
    if found:
        # Deliberately do not delete/replace an existing or previously attempted real assignment.
        write_blocked("real assignment already exists or was previously attempted", found)
        print(json.dumps({"status": "REAL_ASSIGNMENT_ALREADY_ATTEMPTED", "count": len(found), "evidence": found}, indent=2))
        raise SystemExit(2)
    print(json.dumps({
        "status": "NO_PRIOR_REAL_ASSIGNMENT_ATTEMPT",
        "artifact_name": LOCK_ARTIFACT,
        "current_run_id": current_run_id,
    }, indent=2))


if __name__ == "__main__":
    main()
