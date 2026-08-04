from __future__ import annotations
import argparse,json
from pathlib import Path
from .prepare import prepare_source,prepare_external
from .profile import freeze_source_profile
from .evaluate import run_external

def main():
 p=argparse.ArgumentParser(); s=p.add_subparsers(dest="cmd",required=True)
 a=s.add_parser("prepare-source"); a.add_argument("--input",action="append",required=True); a.add_argument("--output",required=True); a.add_argument("--lock",required=True)
 a=s.add_parser("freeze-source"); a.add_argument("--prepared",required=True); a.add_argument("--lock",required=True); a.add_argument("--output",required=True)
 a=s.add_parser("prepare-external"); a.add_argument("--input",required=True); a.add_argument("--output",required=True); a.add_argument("--protocol",required=True)
 a=s.add_parser("run-external"); a.add_argument("--prepared",required=True); a.add_argument("--profile",required=True); a.add_argument("--protocol",required=True); a.add_argument("--output",required=True); a.add_argument("--bootstrap-iterations",type=int,default=1000)
 x=p.parse_args()
 if x.cmd=="prepare-source":
  lock=json.loads(Path(x.lock).read_text()); r=prepare_source([Path(v) for v in x.input],Path(x.output),lock["source_dataset"]["file_hashes"])
 elif x.cmd=="freeze-source": r=freeze_source_profile(Path(x.prepared)/"features_blind_075.csv",Path(x.prepared)/"labels_sealed.csv",Path(x.lock),Path(x.output))
 elif x.cmd=="prepare-external":
  protocol=json.loads(Path(x.protocol).read_text()); r=prepare_external(Path(x.input),Path(x.output),protocol["external_dataset"]["included_sources"])
 else: r=run_external(Path(x.prepared)/"features_blind_075.csv",Path(x.prepared)/"labels_sealed.csv",Path(x.profile),Path(x.protocol),Path(x.output),x.bootstrap_iterations)
 print(json.dumps(r,indent=2,sort_keys=True))
if __name__=="__main__": main()
