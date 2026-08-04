from __future__ import annotations
import csv,json
from datetime import datetime,timezone
from pathlib import Path
from typing import Any,Iterable
import pandas as pd
from public_trajectory_audit.data_io import iter_rows
from public_trajectory_audit.features import extract_prefix
from public_trajectory_audit.nebius import blind_record,label_row,sanitize_row
from .adapter import ALLOWED_SOURCES,EXCLUDED_SOURCES,iter_external_rows,record_and_label
from .canonical import sha256_file,write_json

def now()->str:return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z")

def _write(rows:Iterable[tuple[dict[str,Any],dict[str,Any]]],out:Path,receipt_extra:dict[str,Any])->dict[str,Any]:
    out.mkdir(parents=True,exist_ok=True); fp=out/"features_blind_075.csv"; lp=out/"labels_sealed.csv"
    ft=fp.with_suffix(".tmp"); lt=lp.with_suffix(".tmp")
    fw=lw=None; n=0; seen=set(); cohorts={}
    with ft.open("w",newline="",encoding="utf-8") as fs,lt.open("w",newline="",encoding="utf-8") as ls:
        for feature,label in rows:
            tid=label["trajectory_id"]
            if tid in seen: raise ValueError("duplicate trajectory id")
            seen.add(tid)
            if fw is None: fw=csv.DictWriter(fs,fieldnames=list(feature)); fw.writeheader()
            if lw is None: lw=csv.DictWriter(ls,fieldnames=list(label)); lw.writeheader()
            fw.writerow(feature); lw.writerow(label); n+=1
            cohort=label.get("source_dataset")
            if cohort: cohorts[cohort]=cohorts.get(cohort,0)+1
    if not n: raise ValueError("no rows prepared")
    ft.replace(fp); lt.replace(lp)
    receipt={"schema":"coherence-dynamics.external-replication.prepared-binding.v1","created_at_utc":now(),"rows":n,"horizon":0.75,"features_sha256":sha256_file(fp),"labels_sha256":sha256_file(lp),"cohort_rows":cohorts,"api_or_model_calls":0,"api_credit_spend_usd":0.0,**receipt_extra}
    write_json(out/"FEATURE_LABEL_BINDING.json",receipt); return receipt

def prepare_source(paths:list[Path],out:Path,expected_hashes:dict[str,str])->dict[str,Any]:
    actual={}
    for path in paths:
        key=f"data/{path.name}"; h=sha256_file(path); actual[key]=h
        if expected_hashes.get(key)!=h: raise ValueError(f"source shard hash mismatch: {key}")
    def rows():
        for path in paths:
            for raw in iter_rows(path):
                rec=blind_record(sanitize_row(raw)); feature=extract_prefix(rec,0.75); label=label_row(raw)
                yield feature,label
    return _write(rows(),out,{"dataset_role":"source_profile_reconstruction","source_hashes":actual})

def prepare_external(path:Path,out:Path,expected_counts:dict[str,int])->dict[str,Any]:
    source_hash=sha256_file(path)
    def rows():
        for raw in iter_external_rows(path):
            if raw["source_dataset"] in EXCLUDED_SOURCES: continue
            rec,label=record_and_label(raw); feature=extract_prefix(rec,0.75)
            yield feature,label
    receipt=_write(rows(),out,{"dataset_role":"external_replication","source_file_sha256":source_hash,"excluded_sources":sorted(EXCLUDED_SOURCES)})
    if receipt["cohort_rows"]!=expected_counts: raise ValueError(f"external cohort counts mismatch: {receipt['cohort_rows']} != {expected_counts}")
    return receipt
