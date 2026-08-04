from __future__ import annotations
import json, math
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd
from public_trajectory_audit.modeling import fit_family, metrics, threshold_at_fpr
from public_trajectory_audit.split import repository_holdout
from .canonical import sha256_file, write_json

def _close(a:float,b:float,tol:float=1e-12)->bool: return abs(float(a)-float(b))<=tol

def serialize_pipeline(fitted, threshold:float)->dict[str,Any]:
    numeric=fitted.model.named_steps["prep"].named_transformers_["numeric"]
    imputer=numeric.named_steps["impute"]; scaler=numeric.named_steps["scale"]; model=fitted.model.named_steps["model"]
    return {
      "family":fitted.family,"features":fitted.columns,"selected_C":fitted.C,"threshold":threshold,
      "imputer_statistics":[float(x) for x in imputer.statistics_],
      "scaler_mean":[float(x) for x in scaler.mean_],"scaler_scale":[float(x) for x in scaler.scale_],
      "coefficients":[float(x) for x in model.coef_[0]],"intercept":float(model.intercept_[0]),
      "class_order":[int(x) for x in model.classes_],"solver":"liblinear","class_weight":"balanced",
    }

def apply_profile(frame:pd.DataFrame,profile:dict[str,Any])->np.ndarray:
    cols=profile["features"]
    missing=[c for c in cols if c not in frame]
    if missing: raise ValueError(f"external features missing frozen columns: {missing}")
    x=frame[cols].apply(pd.to_numeric,errors="coerce").to_numpy(dtype=float)
    stats=np.asarray(profile["imputer_statistics"],dtype=float)
    bad=~np.isfinite(x)
    if bad.any(): x[bad]=np.take(stats,np.where(bad)[1])
    mean=np.asarray(profile["scaler_mean"],dtype=float); scale=np.asarray(profile["scaler_scale"],dtype=float)
    z=(x-mean)/scale
    logit=z@np.asarray(profile["coefficients"],dtype=float)+float(profile["intercept"])
    logit=np.clip(logit,-709,709)
    return 1.0/(1.0+np.exp(-logit))

def freeze_source_profile(features_path:Path,labels_path:Path,lock_path:Path,output_path:Path)->dict[str,Any]:
    lock=json.loads(lock_path.read_text())
    features=pd.read_csv(features_path); labels=pd.read_csv(labels_path)
    frame=features.merge(labels[["trajectory_id","target"]],on="trajectory_id",validate="one_to_one")
    if len(frame)!=lock["source_dataset"]["expected_rows"]: raise ValueError("source row count differs from frozen audit")
    train_idx,test_idx=repository_holdout(frame,random_state=lock["split"]["repository_holdout_random_state"],test_size=lock["split"]["test_size"])
    dev=frame.loc[train_idx].reset_index(drop=True); test=frame.loc[test_idx].reset_index(drop=True)
    result={"schema":"coherence-dynamics.external-replication.frozen-profile.v1","source_audit_result_sha256":lock["source_audit_result_sha256"],"horizon":0.75,"development_rows":len(dev),"source_holdout_rows":len(test),"families":{}}
    for family in ("simple","simple_cd"):
        fitted=fit_family(dev,family); expected=lock["families"][family]
        if fitted.columns!=expected["features"]: raise ValueError(f"{family} feature order mismatch")
        if float(fitted.C)!=float(expected["selected_C"]): raise ValueError(f"{family} C mismatch")
        prob=fitted.model.predict_proba(test)[:,1]; observed=metrics(test["target"],prob)
        for metric,value in expected["source_heldout"].items():
            if not _close(observed[metric],value): raise ValueError(f"{family} source metric mismatch {metric}: {observed[metric]} != {value}")
        threshold=threshold_at_fpr(dev["target"],fitted.oof_predictions,max_fpr=0.20)
        if not _close(threshold,expected["source_threshold"]): raise ValueError(f"{family} threshold mismatch")
        result["families"][family]=serialize_pipeline(fitted,threshold)
        result["families"][family]["source_heldout_metrics"]=observed
    result["profile_lock_sha256"]=sha256_file(lock_path)
    write_json(output_path,result)
    return result
