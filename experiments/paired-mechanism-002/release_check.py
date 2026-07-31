from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from common import (
    LINEAGE_SHA256,
    PARENT_MAP_SHA256,
    SCIENTIFIC_HASHES,
    pretty_json_bytes,
    sha256_file,
)

ROOT=Path(__file__).resolve().parent
REPO_ROOT=ROOT.parents[1]
FROZEN=ROOT/'frozen_scientific'


def main():
    checks=[]
    for name,expected in SCIENTIFIC_HASHES.items():
        actual=sha256_file(FROZEN/name)
        if actual!=expected: raise SystemExit(f'scientific hash mismatch: {name}')
        checks.append({'check':f'scientific_hash:{name}','status':'PASS','sha256':actual})
    for name,expected in [('PARENT_MAP_FROZEN_002.json',PARENT_MAP_SHA256),('LINEAGE_001_ABORT.json',LINEAGE_SHA256)]:
        actual=sha256_file(ROOT/name)
        if actual!=expected: raise SystemExit(f'hash mismatch: {name}')
        checks.append({'check':f'hash:{name}','status':'PASS','sha256':actual})

    manifest=json.loads((ROOT/'RUNNER_MANIFEST.json').read_text('utf-8'))
    for rel,expected in manifest.get('runner_files',{}).items():
        actual=sha256_file(ROOT/rel)
        if actual!=expected: raise SystemExit(f'runner manifest mismatch: {rel}')
    for rel,expected in manifest.get('workflow_files',{}).items():
        actual=sha256_file(REPO_ROOT/rel)
        if actual!=expected: raise SystemExit(f'workflow manifest mismatch: {rel}')
    checks.append({'check':'runner_manifest','status':'PASS','runner_files':len(manifest.get('runner_files',{})),'workflow_files':len(manifest.get('workflow_files',{}))})

    proc=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-v'],cwd=ROOT,text=True,capture_output=True,check=False)
    if proc.returncode!=0:
        print(proc.stdout); print(proc.stderr,file=sys.stderr); raise SystemExit(proc.returncode)
    passed=sum(1 for line in proc.stderr.splitlines() if line.rstrip().endswith('... ok'))
    checks.append({'check':'unit_tests','status':'PASS','passed':passed})

    comp=subprocess.run([sys.executable,'-m','compileall','-q','.'],cwd=ROOT,check=False)
    if comp.returncode!=0: raise SystemExit('compileall failed')
    checks.append({'check':'compileall','status':'PASS'})

    with tempfile.TemporaryDirectory(prefix='olp002-dry-preflight-') as td:
        out=Path(td)/'nested'/'build'/'PREFLIGHT_002_DRY.json'
        dry=subprocess.run([sys.executable,'preflight_002.py','--out',str(out),'--dry-run-no-external'],cwd=ROOT,text=True,capture_output=True,check=False)
        if dry.returncode!=0:
            print(dry.stdout); print(dry.stderr,file=sys.stderr); raise SystemExit('002 dry preflight failed')
        obj=json.loads(out.read_text('utf-8'))
        if obj.get('status')!='PREFLIGHT_002_DRY_RUN_PASS' or obj.get('real_assignment_created') is not False or obj.get('benchmark_model_calls')!=0 or obj.get('unblinded') is not False:
            raise SystemExit('002 dry preflight semantic mismatch')
        checks.append({'check':'dry_preflight_nested_output','status':'PASS','disposition':obj.get('disposition')})

    forbidden_names={'secret_key.bin','condition_map.enc','blinded_run_manifest.json','ASSIGNMENT_LOCK.json'}
    present=[str(p.relative_to(ROOT)) for p in ROOT.rglob('*') if p.is_file() and p.name in forbidden_names]
    if present: raise SystemExit(f'real/private assignment artifacts present: {present}')
    if (ROOT/'build/assignment').exists(): raise SystemExit('real assignment output tree exists')
    checks.append({'check':'real_assignment_absent','status':'PASS'})

    report={
        'schema':'openline.paired-mechanism-benchmark.002-dry-run.v1',
        'experiment_id':'olp-core21-paired-mechanism-002',
        'status':'DRY_RUN_PASS',
        'checks':checks,
        'scientific_content_changed':False,
        'scientific_payload_byte_identical_to_001':True,
        '001_scored':False,
        '001_unblinded':False,
        '002_real_assignment_created':False,
        'benchmark_model_calls':0,
        'capacity_probe_model_calls_made_during_local_release_check':0,
        'unblinded':False,
        'statements':['REAL_ASSIGNMENT_NOT_CREATED','BENCHMARK_MODEL_CALLS_0','UNBLINDED_FALSE'],
    }
    (ROOT/'DRY_RUN_RECEIPT.json').write_bytes(pretty_json_bytes(report))
    print(json.dumps({'status':'DRY_RUN_PASS','tests_passed':passed,'receipt':'DRY_RUN_RECEIPT.json'},indent=2))

if __name__=='__main__': main()
