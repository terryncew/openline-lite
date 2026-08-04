from __future__ import annotations
import hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
REQUIRED=["REPLICATION_PROTOCOL.json","SOURCE_PROFILE_LOCK.json","RESULT_RULE.json","SOURCE_REGISTER.json","RUNTIME_LOCK.json","RUNTIME_REPAIR_RECEIPT.json","ORIGINAL_AUDIT_PIP_FREEZE.txt","requirements.txt","src/external_replication/adapter.py","src/external_replication/profile.py","src/external_replication/prepare.py","src/external_replication/evaluate.py","src/external_replication/runner.py","scripts/acquire_external.py","scripts/acquire_nebius.py","scripts/verify_runtime.py","tests/test_replication.py"]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 missing=[x for x in REQUIRED if not (ROOT/x).is_file()]
 if missing: raise SystemExit(f"missing files: {missing}")
 protocol=json.loads((ROOT/"REPLICATION_PROTOCOL.json").read_text()); lock=ROOT/"SOURCE_PROFILE_LOCK.json"
 if protocol["source_profile_lock_sha256"]!=sha(lock): raise SystemExit("source profile lock hash mismatch")
 if protocol["status"]!="FROZEN_BEFORE_EXTERNAL_EXECUTION": raise SystemExit("protocol not frozen")
 if protocol["external_dataset"]["excluded_sources"]!=["nebius-swe-rebench-openhands"]: raise SystemExit("Nebius overlap exclusion changed")
 runtime=json.loads((ROOT/"RUNTIME_LOCK.json").read_text())
 if runtime["python"]["version"]!="3.11.15": raise SystemExit("original Python runtime pin changed")
 if runtime["packages"]["numpy"]!="2.4.6" or runtime["packages"]["scikit-learn"]!="1.9.0": raise SystemExit("original numerical runtime pins changed")
 if runtime["failed_replication_attempt"]["source_tolerance_unchanged"]!=1e-12: raise SystemExit("source tolerance changed")
 if sha(ROOT/"ORIGINAL_AUDIT_PIP_FREEZE.txt")!=runtime["source_pip_freeze_sha256"]: raise SystemExit("original pip freeze hash mismatch")
 print(json.dumps({"status":"PASS","required_files":len(REQUIRED),"protocol_sha256":sha(ROOT/"REPLICATION_PROTOCOL.json"),"profile_lock_sha256":sha(lock),"runtime_lock_sha256":sha(ROOT/"RUNTIME_LOCK.json")},indent=2,sort_keys=True))
if __name__=="__main__":main()
