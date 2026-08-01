from __future__ import annotations
import json, os, sys, urllib.request
from pathlib import Path

LOCK_ARTIFACT="olp-30pair-003-assignment-lock"
ASSIGN_JOB_NAME="assign-once"
WORKFLOW_NAME="OLP 30-pair 003 real execution — exact tag only"
WORKFLOW_PATH="olp-30pair-003-execution.yml"


def api_json(url:str,token:str)->dict:
    req=urllib.request.Request(url,headers={"Authorization":f"Bearer {token}","Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28"})
    with urllib.request.urlopen(req,timeout=30) as resp: return json.load(resp)


def prior_assignment_evidence(repo:str,token:str,current_run_id:str)->list[dict]:
    evidence=[]; page=1
    while True:
        arts=api_json(f"https://api.github.com/repos/{repo}/actions/artifacts?per_page=100&page={page}",token).get("artifacts",[])
        for a in arts:
            if a.get("name")==LOCK_ARTIFACT and not a.get("expired",False): evidence.append({"kind":"003_assignment_lock_artifact","artifact_id":a.get("id"),"workflow_run_id":(a.get("workflow_run") or {}).get("id")})
        if len(arts)<100: break
        page+=1
    page=1
    while True:
        runs=api_json(f"https://api.github.com/repos/{repo}/actions/runs?event=push&per_page=100&page={page}",token).get("workflow_runs",[])
        for run in runs:
            rid=str(run.get("id", ""))
            if not rid or rid==current_run_id: continue
            path=str(run.get("path") or ""); name=str(run.get("name") or "")
            # Intentionally ignores every 001 and 002 workflow/run/artifact.
            if not (path.endswith(WORKFLOW_PATH) or name==WORKFLOW_NAME): continue
            jobs=api_json(f"https://api.github.com/repos/{repo}/actions/runs/{rid}/jobs?per_page=100",token).get("jobs",[])
            for job in jobs:
                if job.get("name")!=ASSIGN_JOB_NAME: continue
                if job.get("conclusion")!="skipped": evidence.append({"kind":"003_prior_assign_job_attempt","workflow_run_id":run.get("id"),"job_id":job.get("id"),"job_conclusion":job.get("conclusion")})
        if len(runs)<100: break
        page+=1
    return evidence


def main():
    repo=os.environ.get("GITHUB_REPOSITORY",""); token=os.environ.get("GITHUB_TOKEN",""); current=os.environ.get("GITHUB_RUN_ID","")
    if not repo or not token or not current: raise SystemExit("003 assignment guard missing GitHub repository/token/run id")
    found=prior_assignment_evidence(repo,token,current)
    if found:
        print(json.dumps({"status":"003_REAL_ASSIGNMENT_ALREADY_ATTEMPTED","evidence":found},indent=2)); raise SystemExit(2)
    print(json.dumps({"status":"NO_PRIOR_003_REAL_ASSIGNMENT_ATTEMPT","artifact_name":LOCK_ARTIFACT,"current_run_id":current},indent=2))

if __name__=="__main__": main()
