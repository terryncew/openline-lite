from __future__ import annotations
import argparse,json
from datetime import datetime,timezone
from pathlib import Path
from huggingface_hub import hf_hub_download
from external_replication.canonical import sha256_file,write_json

def main():
 p=argparse.ArgumentParser(); p.add_argument("--output",required=True); p.add_argument("--revision",required=True); a=p.parse_args(); out=Path(a.output); out.mkdir(parents=True,exist_ok=True)
 downloaded=Path(hf_hub_download(repo_id="thoughtworks/agentic-coding-trajectories",repo_type="dataset",filename="sessions.parquet",revision=a.revision,local_dir=out))
 dest=out/"sessions.parquet"
 if downloaded.resolve()!=dest.resolve(): dest.write_bytes(downloaded.read_bytes())
 manifest={"schema":"coherence-dynamics.external-replication.data-manifest.v1","repo_id":"thoughtworks/agentic-coding-trajectories","requested_revision":a.revision,"file":"sessions.parquet","bytes":dest.stat().st_size,"sha256":sha256_file(dest),"created_at_utc":datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z"),"model_api_calls":0,"api_credit_spend_usd":0.0}
 write_json(out/"DATA_MANIFEST.json",manifest); print(json.dumps(manifest,indent=2,sort_keys=True))
if __name__=="__main__":main()
