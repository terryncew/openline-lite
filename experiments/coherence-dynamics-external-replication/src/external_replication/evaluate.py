from __future__ import annotations
import json,math
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score,brier_score_loss,roc_auc_score
from public_trajectory_audit.modeling import operating_metrics
from .canonical import sha256_file,write_json
from .profile import apply_profile

def metrics(y,prob):
    return {"pr_auc":float(average_precision_score(y,prob)),"roc_auc":float(roc_auc_score(y,prob)),"brier":float(brier_score_loss(y,prob))}

def bootstrap(frame,base,extended,iterations=1000,seed=20260804):
    rng=np.random.default_rng(seed); groups=frame["task_group"].drop_duplicates().to_numpy(); arr=frame["task_group"].to_numpy(); positions={g:np.flatnonzero(arr==g) for g in groups}; y=frame["target"].to_numpy(); vals=[]
    for _ in range(iterations):
        sampled=rng.choice(groups,size=len(groups),replace=True); idx=np.concatenate([positions[g] for g in sampled]); yy=y[idx]
        if len(np.unique(yy))<2: continue
        vals.append(float(average_precision_score(yy,extended[idx])-average_precision_score(yy,base[idx])))
    return {"iterations":len(vals),"lower_95":float(np.quantile(vals,.025)),"median":float(np.quantile(vals,.5)),"upper_95":float(np.quantile(vals,.975))}

def disposition(delta,roc_delta,interval,cohort_deltas):
    overall=delta>0.02 and interval["lower_95"]>0 and roc_delta>=-0.005
    if overall and all(v>0 for v in cohort_deltas.values()): return "CD_ADDS_EXTERNAL_SIGNAL"
    if overall: return "MIXED_EXTERNAL_SIGNAL"
    if delta < -0.01 and interval["upper_95"]<0: return "BASELINE_OUTPERFORMS_CD"
    if abs(delta)<=0.01 and interval["lower_95"]<=0<=interval["upper_95"]: return "BASELINE_EQUIVALENT"
    return "NO_RELIABLE_EXTERNAL_SIGNAL"

def run_external(features_path:Path,labels_path:Path,profile_path:Path,protocol_path:Path,output:Path,iterations:int=1000)->dict[str,Any]:
    f=pd.read_csv(features_path); l=pd.read_csv(labels_path); frame=f.merge(l,on=["trajectory_id","instance_id","repository","model_name"],validate="one_to_one")
    profile=json.loads(profile_path.read_text()); protocol=json.loads(protocol_path.read_text())
    simple=apply_profile(frame,profile["families"]["simple"]); extended=apply_profile(frame,profile["families"]["simple_cd"])
    sm=metrics(frame["target"],simple); em=metrics(frame["target"],extended); delta=em["pr_auc"]-sm["pr_auc"]; roc_delta=em["roc_auc"]-sm["roc_auc"]
    interval=bootstrap(frame,simple,extended,iterations=iterations)
    cohorts={}
    for cohort,sub in frame.groupby("source_dataset",sort=True):
        idx=sub.index.to_numpy(); a=metrics(sub["target"],simple[idx]); b=metrics(sub["target"],extended[idx]); cohorts[cohort]={"rows":len(sub),"success_rate":float(sub["target"].mean()),"simple":a,"simple_cd":b,"pr_auc_delta":b["pr_auc"]-a["pr_auc"],"roc_auc_delta":b["roc_auc"]-a["roc_auc"]}
    cohort_deltas={k:v["pr_auc_delta"] for k,v in cohorts.items()}
    disp=disposition(delta,roc_delta,interval,cohort_deltas)
    binding_path=features_path.parent/"FEATURE_LABEL_BINDING.json"
    schema_audit_path=features_path.parent/"EXTERNAL_SCHEMA_AUDIT.json"
    if not binding_path.is_file() or not schema_audit_path.is_file(): raise ValueError("external preparation binding or schema audit missing")
    result={"schema":"coherence-dynamics.external-replication.result.v3","replication_id":protocol["replication_id"],"disposition":disp,"rows":len(frame),"success_rate":float(frame["target"].mean()),"horizon":0.75,"simple":sm,"simple_cd":em,"pr_auc_delta":delta,"roc_auc_delta":roc_delta,"task_group_bootstrap":interval,"cohorts":cohorts,"source_threshold_operating_points":{"simple":operating_metrics(frame["target"],simple,float(profile["families"]["simple"]["threshold"])),"simple_cd":operating_metrics(frame["target"],extended,float(profile["families"]["simple_cd"]["threshold"]))},"source_profile_kind":profile.get("profile_kind"),"profile_sha256":sha256_file(profile_path),"protocol_sha256":sha256_file(protocol_path),"feature_label_binding_sha256":sha256_file(binding_path),"external_schema_audit_sha256":sha256_file(schema_audit_path),"claim_boundary":protocol["claim_boundary"],"api_or_model_calls":0,"api_credit_spend_usd":0.0}
    output.mkdir(parents=True,exist_ok=True); h=write_json(output/"EXTERNAL_REPLICATION_RESULT.json",result)
    receipt={"schema":"coherence-dynamics.external-replication.run-receipt.v3","replication_id":protocol["replication_id"],"disposition":disp,"rows":len(frame),"result_sha256":h,"profile_sha256":sha256_file(profile_path),"features_sha256":sha256_file(features_path),"labels_sha256":sha256_file(labels_path),"feature_label_binding_sha256":sha256_file(binding_path),"external_schema_audit_sha256":sha256_file(schema_audit_path),"bootstrap_iterations_requested":iterations,"model_api_calls":0,"api_credit_spend_usd":0.0}
    write_json(output/"RUN_RECEIPT.json",receipt)
    md=(f"# Coherence Dynamics External Replication\n\n**Disposition:** `{disp}`\n\n"
        f"External trajectories: **{len(frame):,}**\n\n"
        "| Model | PR-AUC | ROC-AUC | Brier |\n|---|---:|---:|---:|\n"
        f"| Frozen simple | {sm['pr_auc']:.4f} | {sm['roc_auc']:.4f} | {sm['brier']:.4f} |\n"
        f"| Frozen simple + CD | {em['pr_auc']:.4f} | {em['roc_auc']:.4f} | {em['brier']:.4f} |\n\n"
        f"PR-AUC delta: **{delta:+.4f}**  \n"
        f"95% task-bootstrap interval: **[{interval['lower_95']:+.4f}, {interval['upper_95']:+.4f}]**\n\n"
        "No external fitting, model API calls, or API credit spend occurred.\n")
    (output/"EXECUTION_SUMMARY.md").write_text(md,encoding="utf-8")
    return result
