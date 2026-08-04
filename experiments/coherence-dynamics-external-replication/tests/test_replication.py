from __future__ import annotations
import json,math,tempfile,unittest
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer
from external_replication.adapter import normalize_messages,repository_from_external,record_and_label,ExternalSchemaError
from external_replication.profile import apply_profile, serialize_pipeline
from external_replication.evaluate import disposition,bootstrap

class FakeFitted:
 def __init__(self,model,cols): self.family="simple"; self.columns=cols; self.C=1.0; self.model=model

class Tests(unittest.TestCase):
 def test_message_normalization(self):
  raw=json.dumps([{"role":"assistant","content":"","tool_calls_json":json.dumps([{"function":{"name":"pytest","arguments":"-q"}}])},{"role":"tool","content":"1 passed"}])
  out=normalize_messages(raw); self.assertEqual(out[0]["role"],"ai"); self.assertIn("pytest",out[0]["text"]); self.assertEqual(out[1]["role"],"user")
 def test_repository(self): self.assertEqual(repository_from_external("iterative__dvc.1d6ea681.pr_3727.x"),"iterative__dvc")
 def test_excludes_nebius(self):
  row={"session_id":"x","source_dataset":"nebius-swe-rebench-openhands","source_id":"x","recorded_model":"m","messages_json":"[]","ground_truth_meta_json":"{\"resolved\":true}"}
  with self.assertRaises(ExternalSchemaError): record_and_label(row)
 def test_label_and_identity(self):
  row={"session_id":"s","source_dataset":"swe-smith-claude-3-7-sonnet","source_id":"owner__repo.x","recorded_model":"m","messages_json":json.dumps([{"role":"assistant","content":"pytest -q"},{"role":"tool","content":"1 passed"}]),"ground_truth_meta_json":json.dumps({"resolved":True,"instance_id":"owner__repo.x"})}
  rec,label=record_and_label(row); self.assertEqual(label["target"],1); self.assertEqual(rec.repository,"owner__repo")
 def test_frozen_application_matches_sklearn(self):
  x=pd.DataFrame({"a":[1.,2.,np.nan,4.],"b":[0.,1.,2.,3.]}); y=[0,0,1,1]; cols=["a","b"]
  prep=ColumnTransformer([("numeric",Pipeline([("impute",SimpleImputer(strategy="median",keep_empty_features=True)),("scale",StandardScaler())]),cols)],remainder="drop")
  pipe=Pipeline([("prep",prep),("model",LogisticRegression(C=1.0,class_weight="balanced",max_iter=3000,solver="liblinear",random_state=20260804))]); pipe.fit(x,y)
  prof=serialize_pipeline(pipe, family="simple", columns=cols, C=1.0, threshold=0.5); np.testing.assert_allclose(apply_profile(x,prof),pipe.predict_proba(x)[:,1],rtol=0,atol=1e-15)
 def test_positive_disposition(self): self.assertEqual(disposition(.03,0,{"lower_95":.01,"upper_95":.05},{"a":.01,"b":.02}),"CD_ADDS_EXTERNAL_SIGNAL")
 def test_mixed_disposition(self): self.assertEqual(disposition(.03,0,{"lower_95":.01,"upper_95":.05},{"a":.01,"b":-.001}),"MIXED_EXTERNAL_SIGNAL")
 def test_negative_disposition(self): self.assertEqual(disposition(-.02,0,{"lower_95":-.03,"upper_95":-.01},{"a":-.02,"b":-.01}),"BASELINE_OUTPERFORMS_CD")
 def test_equivalent_disposition(self): self.assertEqual(disposition(.005,0,{"lower_95":-.01,"upper_95":.02},{"a":0,"b":0}),"BASELINE_EQUIVALENT")
 def test_bootstrap_deterministic(self):
  f=pd.DataFrame({"task_group":["a","a","b","b"],"target":[0,1,0,1]}); b=np.array([.1,.8,.2,.7]); e=np.array([.1,.9,.1,.8]); self.assertEqual(bootstrap(f,b,e,50),bootstrap(f,b,e,50))
 def test_protocol_frozen(self):
  root=Path(__file__).resolve().parents[1]; p=json.loads((root/"REPLICATION_PROTOCOL.json").read_text()); self.assertEqual(p["status"],"FROZEN_AFTER_SOURCE_PROFILE_RECOVERY_REPAIR_BEFORE_EXTERNAL_ACQUISITION"); self.assertEqual(p["frozen_horizon"],.75)
 def test_source_overlap_excluded(self):
  root=Path(__file__).resolve().parents[1]; p=json.loads((root/"REPLICATION_PROTOCOL.json").read_text()); self.assertEqual(p["external_dataset"]["excluded_sources"],["nebius-swe-rebench-openhands"])
 def test_no_external_refit_language(self):
  root=Path(__file__).resolve().parents[1]; p=json.loads((root/"REPLICATION_PROTOCOL.json").read_text()); self.assertIn("No external fitting",p["modeling"])

 def test_source_recovery_protocol(self):
  root=Path(__file__).resolve().parents[1]; r=json.loads((root/"SOURCE_PROFILE_RECOVERY.json").read_text()); self.assertEqual(r["status"],"FROZEN_AFTER_TWO_SOURCE_REPRODUCTION_ABORTS_BEFORE_EXTERNAL_ACQUISITION"); self.assertTrue(r["external_blindness"]["profile_must_be_written_and_hashed_before_external_acquisition"]); self.assertEqual(r["source_metric_sanity"]["max_absolute_delta_each_metric"],1e-4)
 def test_source_lock_remains_historical(self):
  root=Path(__file__).resolve().parents[1]; lock=json.loads((root/"SOURCE_PROFILE_LOCK.json").read_text()); self.assertEqual(lock["freeze_rule"].split("within ")[-1],"1e-12.")
 def test_workflow_seals_profile_before_external(self):
  root=Path(__file__).resolve().parents[3]; w=(root/".github/workflows/cd-external-replication.yml").read_text(); self.assertIn("verify_recovered_profile.py",w); self.assertLess(w.index("Verify source profile was sealed before external access"),w.index("Acquire pinned external dataset"))
 def test_no_cv_reselection_in_recovery(self):
  root=Path(__file__).resolve().parents[1]; p=(root/"src/external_replication/profile.py").read_text(); self.assertNotIn("fit_family",p); self.assertIn("pipeline(columns, C=C)",p)

 def test_runtime_lock_retained_not_identity(self):
  root=Path(__file__).resolve().parents[1]; r=json.loads((root/"RUNTIME_LOCK.json").read_text()); self.assertEqual(r["status"],"PINNED_RUNTIME_RETAINED_FOR_EXECUTION_NOT_USED_AS_MODEL_IDENTITY"); self.assertFalse(r["second_runtime_pinned_attempt"]["external_dataset_acquired"])

if __name__=="__main__": unittest.main()
