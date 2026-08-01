from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

from api_retry import JSONTransportResult, ResponsesAPIError, RetryingJSONTransport
from assignment import generate_assignment
from capacity_probe import CapacityProbeResponseError, run_capacity_probe
from collect_execution import collect
from common import (
    API_RETRY_MAX_ATTEMPTS,
    EXPERIMENT_ID,
    MAX_OUTPUT_TOKENS,
    PAIR_CONTROLLED_WORST_CASE_SECONDS,
    PAIR_JOB_TIMEOUT_SECONDS,
    PAIR_MATRIX_MAX_PARALLEL,
    PINNED_MODEL,
    SCIENTIFIC_HASHES,
    SOURCE_EXPERIMENT_ID,
    canonical_json_bytes,
    load_json,
    sha256_file,
)
from execute_pair import PairInfrastructureFailure, write_infrastructure_receipt
from perturbation import OneShotEligibleReadDelivery, final_quarter_truncate
from responses_agent import ResponsesClient, function_calls
from tool_runtime import ToolRuntime
from trace_format import assert_export_safe


class FakeResponse:
    def __init__(self, obj, status=200):
        self.status = status
        self._data = json.dumps(obj).encode()
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return self._data


def http_error(status: int, *, error_type="rate_limit_error", code="rate_limit_exceeded", retry_after=None):
    body = json.dumps({"error": {"message": "DO_NOT_PERSIST_THIS_MESSAGE", "type": error_type, "code": code}}).encode()
    headers = {} if retry_after is None else {"Retry-After": str(retry_after)}
    return urllib.error.HTTPError("https://api.openai.com/v1/responses", status, "x", headers, io.BytesIO(body))


class Test002(unittest.TestCase):
    def test_scientific_payload_hashes_byte_identical_and_source_identity(self):
        frozen = ROOT / "frozen_scientific"
        for name, expected in SCIENTIFIC_HASHES.items():
            self.assertEqual(sha256_file(frozen / name), expected)
        pair = load_json(frozen / "PAIR_SET_FROZEN.json")
        self.assertEqual(pair["experiment_id"], SOURCE_EXPERIMENT_ID)
        self.assertEqual(len(pair["pairs"]), 30)
        self.assertEqual(EXPERIMENT_ID, "olp-core21-paired-mechanism-002")

    def test_429_transient_retries_with_deterministic_backoff(self):
        seq = [http_error(429, retry_after=1), http_error(429), FakeResponse({"status":"completed","model":PINNED_MODEL,"id":"r","output":[]})]
        sleeps=[]
        def opener(req, timeout):
            item=seq.pop(0)
            if isinstance(item, Exception): raise item
            return item
        tr=RetryingJSONTransport(urlopen_fn=opener, sleep_fn=sleeps.append)
        req=mock.Mock()
        result=tr.request(req,total_timeout_seconds=100)
        self.assertEqual(result.attempts,3)
        self.assertEqual(sleeps,[2.0,4.0])
        self.assertEqual([e["http_status"] for e in result.retry_events],[429,429])
        self.assertNotIn("DO_NOT_PERSIST_THIS_MESSAGE",json.dumps(result.retry_events))

    def test_429_quota_exhaustion_is_not_retried(self):
        sleeps=[]
        def opener(req, timeout): raise http_error(429,error_type="insufficient_quota",code="insufficient_quota")
        tr=RetryingJSONTransport(urlopen_fn=opener,sleep_fn=sleeps.append)
        with self.assertRaises(ResponsesAPIError) as cm:
            tr.request(mock.Mock(),total_timeout_seconds=100)
        self.assertEqual(cm.exception.detail.category,"HTTP_429_QUOTA_OR_SPEND_PERMANENT")
        self.assertFalse(cm.exception.detail.retryable)
        self.assertEqual(sleeps,[])

    def test_503_exhausts_exact_retry_budget(self):
        sleeps=[]; calls=[]
        def opener(req, timeout):
            calls.append(1); raise http_error(503,error_type="server_error",code="server_error")
        tr=RetryingJSONTransport(urlopen_fn=opener,sleep_fn=sleeps.append)
        with self.assertRaises(ResponsesAPIError) as cm:
            tr.request(mock.Mock(),total_timeout_seconds=100)
        self.assertEqual(len(calls),API_RETRY_MAX_ATTEMPTS)
        self.assertEqual(sleeps,[2.0,4.0,8.0])
        self.assertEqual(cm.exception.detail.http_status,503)

    def test_timeout_transport_can_retry(self):
        seq=[TimeoutError(),FakeResponse({"status":"completed","model":PINNED_MODEL,"id":"r","output":[]})]; sleeps=[]
        def opener(req,timeout):
            x=seq.pop(0)
            if isinstance(x,Exception): raise x
            return x
        tr=RetryingJSONTransport(urlopen_fn=opener,sleep_fn=sleeps.append)
        result=tr.request(mock.Mock(),total_timeout_seconds=100)
        self.assertEqual(result.attempts,2)
        self.assertEqual(result.retry_events[0]["transport_category"],"TIMEOUT")

    def test_completed_response_not_retried_and_tool_effect_once(self):
        result=JSONTransportResult(obj={
            "status":"completed","model":PINNED_MODEL,"id":"r1",
            "output":[{"type":"function_call","name":"apply_patch","call_id":"c1","arguments":json.dumps({"patch":"*** invalid but counted ***"})}],
        },attempts=3,retry_events=[{"category":"HTTP_429_RATE_LIMIT_TRANSIENT"},{"category":"HTTP_503_SERVER_TRANSIENT"}])
        class T:
            def __init__(self): self.calls=0
            def request(self,req,total_timeout_seconds): self.calls+=1; return result
        t=T(); client=ResponsesClient("k",transport=t)
        response=client.create(instructions="x",history=[],timeout=10)
        self.assertEqual(t.calls,1)
        self.assertEqual(client.completed_response_count,1)
        self.assertEqual(client.api_attempt_count,3)
        self.assertEqual(len(function_calls(response)),1)
        # Retry happens before a completed response reaches the orchestration layer; one response -> one tool effect opportunity.
        effects=0
        for _call in function_calls(response): effects+=1
        self.assertEqual(effects,1)

    def test_capacity_probe_is_non_scientific_and_sequential(self):
        requests=[]
        class T:
            def request(self,req,total_timeout_seconds):
                requests.append(json.loads(req.data))
                i=len(requests)
                return JSONTransportResult(obj={"status":"completed","model":PINNED_MODEL,"id":f"cap-{i}","output":[]},attempts=1,retry_events=[])
        sleeps=[]
        rec=run_capacity_probe(api_key="k",transport=T(),sleep_fn=sleeps.append)
        self.assertEqual(rec["completed_probe_count"],12)
        self.assertEqual(len(requests),12)
        self.assertEqual(len(sleeps),11)
        text=json.dumps(requests)
        self.assertNotIn("P01",text)
        self.assertNotIn("task_commit",text)
        self.assertNotIn('"tools"',text)
        self.assertEqual(rec["benchmark_model_calls"],0)
        self.assertEqual(rec["requested_max_output_tokens"],MAX_OUTPUT_TOKENS)
        self.assertTrue(all(req["max_output_tokens"] == MAX_OUTPUT_TOKENS for req in requests))

    def test_capacity_probe_incomplete_response_is_safely_diagnostic(self):
        raw_output_marker="DO_NOT_PERSIST_RAW_MODEL_OUTPUT"
        result=JSONTransportResult(
            obj={
                "status":"incomplete",
                "model":PINNED_MODEL,
                "id":"resp-cap-incomplete",
                "incomplete_details":{"reason":"max_output_tokens","extra":"DO_NOT_PERSIST_EXTRA"},
                "usage":{
                    "input_tokens":17,
                    "output_tokens":16384,
                    "total_tokens":16401,
                    "input_tokens_details":{"cached_tokens":3,"other":999},
                    "output_tokens_details":{"reasoning_tokens":16384,"other":999},
                    "unsafe":"DO_NOT_PERSIST_USAGE",
                },
                "output":[{"type":"message","content":raw_output_marker}],
            },
            attempts=1,
            retry_events=[],
        )
        class T:
            def request(self,req,total_timeout_seconds): return result
        with self.assertRaises(CapacityProbeResponseError) as cm:
            run_capacity_probe(api_key="k",transport=T(),sleep_fn=lambda _: None)
        self.assertEqual(cm.exception.category,"CAPACITY_RESPONSE_NOT_COMPLETED")
        detail=cm.exception.public_detail
        self.assertEqual(detail["response_status"],"incomplete")
        self.assertEqual(detail["incomplete_reason"],"max_output_tokens")
        self.assertEqual(detail["returned_model"],PINNED_MODEL)
        self.assertEqual(detail["requested_max_output_tokens"],MAX_OUTPUT_TOKENS)
        self.assertEqual(detail["usage"]["output_tokens_details"]["reasoning_tokens"],16384)
        serialized=json.dumps(detail)
        self.assertNotIn(raw_output_marker,serialized)
        self.assertNotIn("DO_NOT_PERSIST_EXTRA",serialized)
        self.assertNotIn("DO_NOT_PERSIST_USAGE",serialized)

    def test_preflight_seals_capacity_application_failure_context(self):
        import preflight_002
        failure=CapacityProbeResponseError("CAPACITY_RESPONSE_NOT_COMPLETED",{
            "response_status":"incomplete",
            "incomplete_reason":"max_output_tokens",
            "returned_model":PINNED_MODEL,
            "requested_max_output_tokens":MAX_OUTPUT_TOKENS,
            "usage":{"output_tokens":MAX_OUTPUT_TOKENS},
        })
        with tempfile.TemporaryDirectory() as td:
            out=Path(td)/"build"/"PREFLIGHT_002_PASS.json"
            with mock.patch.dict(os.environ,{"OPENAI_API_KEY":"test-key"}), mock.patch.object(preflight_002,"run_capacity_probe",side_effect=failure):
                with self.assertRaises(SystemExit):
                    preflight_002.run_preflight(
                        out=out,
                        perform_checkouts=False,
                        perform_network_sandbox=False,
                        perform_capacity_probe=True,
                    )
            blocked=load_json(out.with_name("PREFLIGHT_002_BLOCKED.json"))
            self.assertEqual(blocked["failed_stage"],"capacity_probe")
            self.assertEqual(blocked["failure_reason"],"CAPACITY_RESPONSE_NOT_COMPLETED")
            self.assertEqual(blocked["failure_detail"]["response_failure"]["incomplete_reason"],"max_output_tokens")
            self.assertFalse(blocked["real_assignment_created"])
            self.assertEqual(blocked["benchmark_model_calls"],0)
            self.assertFalse(blocked["unblinded"])

    def test_pair_runtime_bound_stays_below_60_minute_job_timeout(self):
        self.assertEqual(PAIR_MATRIX_MAX_PARALLEL,1)
        self.assertLess(PAIR_CONTROLLED_WORST_CASE_SECONDS,PAIR_JOB_TIMEOUT_SECONDS)
        self.assertGreaterEqual(PAIR_JOB_TIMEOUT_SECONDS-PAIR_CONTROLLED_WORST_CASE_SECONDS,300)

    def test_execution_workflow_is_exact_tag_and_serial_pairs(self):
        text=(ROOT.parents[1]/".github/workflows/olp-30pair-002-execution.yml").read_text()
        self.assertIn('RUN_REAL_OLP_CORE21_PAIRED_MECHANISM_002_RETRY1',text)
        self.assertIn('REAL_RUN_TAG: RUN_REAL_OLP_CORE21_PAIRED_MECHANISM_002_RETRY1',text)
        self.assertNotIn('\"RUN_REAL_OLP_CORE21_PAIRED_MECHANISM_002\"',text)
        self.assertIn('max-parallel: 1',text)
        self.assertNotIn('max-parallel: 3',text)
        self.assertNotIn('workflow_dispatch',text)

    def test_preflight_workflow_has_no_assignment_step(self):
        text=(ROOT.parents[1]/".github/workflows/olp-30pair-002-preflight.yml").read_text()
        self.assertIn('preflight/olp-core21-paired-mechanism-002',text)
        self.assertNotIn('assignment.py',text)
        self.assertNotIn('assign-once:',text)

    def test_guard_002_scope_does_not_match_001_names(self):
        text=(ROOT/"guard_once.py").read_text()
        self.assertIn('olp-30pair-002-assignment-lock',text)
        self.assertIn('olp-30pair-002-execution.yml',text)
        self.assertNotIn('olp-30pair-assignment-lock"',text)

    def test_disposable_002_assignment_accepts_inherited_pair_set_and_is_fresh_balanced(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td); pub=d/'pub'; sealed=d/'sealed'; secret=d/'secret'
            lock=generate_assignment(
                pair_set_path=ROOT/'frozen_scientific/PAIR_SET_FROZEN.json',public_dir=pub,sealed_dir=sealed,secret_dir=secret,
                design_sha256=SCIENTIFIC_HASHES['BENCHMARK_DESIGN_FROZEN.json'],pair_set_sha256=SCIENTIFIC_HASHES['PAIR_SET_FROZEN.json'],
                signal_schema_sha256=SCIENTIFIC_HASHES['SIGNAL_SCHEMA_FROZEN_SCOPE_REPAIRED.json'],perturbation_sha256=SCIENTIFIC_HASHES['PERTURBATION_SPEC_FROZEN_SCOPE_REPAIRED.json'],
                preflight_pass_sha256='a'*64,runner_manifest_sha256='b'*64,dry_run=True)
            self.assertEqual(lock['pair_count'],30); self.assertEqual(lock['execution_count'],60)
            man=load_json(pub/'blinded_run_manifest.json')
            self.assertEqual(man['experiment_id'],EXPERIMENT_ID)
            self.assertEqual(len({r['opaque_execution_id'] for r in man['executions']}),60)

    def test_infrastructure_receipt_preserves_sanitized_api_detail(self):
        with tempfile.TemporaryDirectory() as td:
            f=PairInfrastructureFailure('HTTP_429_RATE_LIMIT_TRANSIENT',detail={
                'failure_class':'MODEL_API','execution_phase':'BRANCH_EXECUTION',
                'api_failure':{'http_status':429,'openai_error_type':'rate_limit_error','openai_error_code':'rate_limit_exceeded','retry_after_header':'2','attempt':4,'timestamp_utc':'x'},
                'api_metrics':{'api_attempt_count':4,'completed_response_count':3,'retry_count':3,'returned_models':[PINNED_MODEL],'failure_events':[]},
            })
            rec=write_infrastructure_receipt(Path(td),'P05',f)
            text=json.dumps(rec)
            self.assertIn('429',text); self.assertIn('rate_limit_exceeded',text)
            self.assertNotIn('CLEAN',text); self.assertNotIn('PERTURBED',text); self.assertNotIn('api_key',text.lower())

    def test_collector_includes_infrastructure_receipts_and_attempt_counts(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td); pub=d/'pub'; sealed=d/'sealed'; secret=d/'secret'
            generate_assignment(
                pair_set_path=ROOT/'frozen_scientific/PAIR_SET_FROZEN.json',public_dir=pub,sealed_dir=sealed,secret_dir=secret,
                design_sha256=SCIENTIFIC_HASHES['BENCHMARK_DESIGN_FROZEN.json'],pair_set_sha256=SCIENTIFIC_HASHES['PAIR_SET_FROZEN.json'],
                signal_schema_sha256=SCIENTIFIC_HASHES['SIGNAL_SCHEMA_FROZEN_SCOPE_REPAIRED.json'],perturbation_sha256=SCIENTIFIC_HASHES['PERTURBATION_SPEC_FROZEN_SCOPE_REPAIRED.json'],
                preflight_pass_sha256='a'*64,runner_manifest_sha256='b'*64,dry_run=True)
            pairs=d/'pairs'; pairs.mkdir()
            f=PairInfrastructureFailure('HTTP_503_SERVER_TRANSIENT',detail={'api_metrics':{'api_attempt_count':4,'completed_response_count':2,'retry_count':3,'returned_models':[PINNED_MODEL],'failure_events':[]}})
            write_infrastructure_receipt(pairs,'P05',f)
            out=d/'out'
            rec=collect(pair_artifacts=pairs,blinded_manifest=pub/'blinded_run_manifest.json',assignment_lock=pub/'ASSIGNMENT_LOCK.json',sealed_condition_zip=sealed/'SEALED_CONDITION_BUNDLE.zip',out_dir=out,runner_manifest_sha256='b'*64,preflight_pass_sha256='a'*64)
            self.assertEqual(rec['status'],'EXECUTION_INCOMPLETE_BLIND')
            self.assertEqual(rec['infrastructure_failure_receipt_count'],1)
            self.assertEqual(rec['benchmark_api_attempt_count'],4)
            self.assertEqual(rec['infrastructure_failure_classes']['HTTP_503_SERVER_TRANSIENT'],1)
            import zipfile
            with zipfile.ZipFile(out/'PUBLIC_SCORER_EXECUTION_BUNDLE.zip') as z:
                self.assertIn('infrastructure/P05.infrastructure.json',z.namelist())

    def test_perturbation_semantics_unchanged(self):
        text='🙂abcdefghi'
        out=final_quarter_truncate(text)
        import math
        self.assertEqual(len(text)-len(out),math.ceil(len(text)/4))
        one=OneShotEligibleReadDelivery(); first=one.deliver(text,alter=True)
        self.assertNotEqual(first,text)
        with self.assertRaises(RuntimeError): one.deliver(text,alter=True)

    def test_public_export_rejects_condition_labels(self):
        with self.assertRaises(ValueError): assert_export_safe({'x':'CLEAN'})
        with self.assertRaises(ValueError): assert_export_safe({'condition':'x'})

    def test_parent_map_has_exact_30_pairs(self):
        obj=load_json(ROOT/'PARENT_MAP_FROZEN_002.json')
        self.assertEqual(len(obj['pairs']),30)
        self.assertEqual(sorted(obj['pairs']),[f'P{i:02d}' for i in range(1,31)])

    def test_package_contains_no_001_secret_or_condition_material(self):
        forbidden={'secret_key.bin','condition_map.enc','blinded_run_manifest.json','ASSIGNMENT_LOCK.json'}
        names={p.name for p in ROOT.rglob('*') if p.is_file()}
        self.assertTrue(forbidden.isdisjoint(names))

    def test_retry_after_is_capped_deterministically(self):
        seq=[http_error(429,retry_after=99),FakeResponse({"status":"completed","model":PINNED_MODEL,"id":"r","output":[]})]; sleeps=[]
        def opener(req,timeout):
            x=seq.pop(0)
            if isinstance(x,Exception): raise x
            return x
        result=RetryingJSONTransport(urlopen_fn=opener,sleep_fn=sleeps.append).request(mock.Mock(),total_timeout_seconds=100)
        self.assertEqual(result.attempts,2)
        self.assertEqual(sleeps,[15.0])

    def test_guard_ignores_001_and_skipped_002_but_blocks_run_002_attempt(self):
        import guard_once
        responses=[]
        def fake(url, token):
            if '/artifacts?' in url: return {'artifacts':[]}
            if '/actions/runs?' in url:
                return {'workflow_runs':[
                    {'id':101,'path':'.github/workflows/olp-30pair-execution.yml','name':'OLP 30-pair real execution — exact tag only'},
                    {'id':202,'path':'.github/workflows/olp-30pair-002-execution.yml','name':'OLP 30-pair 002 real execution — exact tag only'},
                ]}
            if '/runs/202/jobs' in url: return {'jobs':[{'name':'assign-once','conclusion':'skipped','id':1}]}
            if '/runs/101/jobs' in url: return {'jobs':[{'name':'assign-once','conclusion':'success','id':2}]}
            raise AssertionError(url)
        with mock.patch.object(guard_once,'api_json',side_effect=fake):
            self.assertEqual(guard_once.prior_assignment_evidence('r','t','999'),[])
        def fake2(url, token):
            if '/artifacts?' in url: return {'artifacts':[]}
            if '/actions/runs?' in url: return {'workflow_runs':[{'id':202,'path':'.github/workflows/olp-30pair-002-execution.yml','name':'OLP 30-pair 002 real execution — exact tag only'}]}
            if '/runs/202/jobs' in url: return {'jobs':[{'name':'assign-once','conclusion':'failure','id':3}]}
            raise AssertionError(url)
        with mock.patch.object(guard_once,'api_json',side_effect=fake2):
            evidence=guard_once.prior_assignment_evidence('r','t','999')
        self.assertEqual(len(evidence),1)
        self.assertEqual(evidence[0]['kind'],'002_prior_assign_job_attempt')

    def test_dry_preflight_creates_nested_receipt_without_external_calls(self):
        import preflight_002
        with tempfile.TemporaryDirectory() as td:
            out=Path(td)/'a'/'b'/'receipt.json'
            rec,digest=preflight_002.run_preflight(out=out,perform_checkouts=False,perform_network_sandbox=False,perform_capacity_probe=False)
            self.assertTrue(out.exists())
            self.assertEqual(rec['status'],'PREFLIGHT_002_DRY_RUN_PASS')
            self.assertFalse(rec['real_assignment_created'])
            self.assertEqual(rec['benchmark_model_calls'],0)

    def test_execution_bundle_contains_no_scoring_or_unblinding_entrypoint(self):
        workflow=(ROOT.parents[1]/'.github/workflows/olp-30pair-002-execution.yml').read_text().lower()
        self.assertNotIn('score_kappa',workflow)
        self.assertNotIn('decrypt_map',workflow)
        self.assertNotIn('unblind',workflow)

    def test_retry1_preserves_prior_preassignment_failure_receipt(self):
        receipt=ROOT/'RETRY1_PRIOR_PREASSIGNMENT_FAILURE.json'
        sidecar=ROOT/'RETRY1_PRIOR_PREASSIGNMENT_FAILURE.json.sha256'
        self.assertTrue(receipt.exists()); self.assertTrue(sidecar.exists())
        expected=sidecar.read_text('utf-8').split()[0]
        self.assertEqual(sha256_file(receipt),expected)
        self.assertEqual(expected,'3fdbe1621dda0b3aa7dc8f3f46db3cf0bc08449bfb9aace1fd69aa8fe42e641b')
        obj=load_json(receipt)
        self.assertEqual(obj['status'],'PREFLIGHT_002_BLOCKED')
        self.assertEqual(obj['failed_stage'],'capacity_probe')
        self.assertEqual(obj['failure_reason'],'CAPACITY_RESPONSE_NOT_COMPLETED')
        self.assertFalse(obj['real_assignment_created'])
        self.assertEqual(obj['benchmark_model_calls'],0)
        self.assertFalse(obj['unblinded'])

    def test_no_real_assignment_artifact_exists_after_tests(self):
        self.assertFalse((ROOT/'build/assignment').exists())


if __name__=='__main__': unittest.main()
