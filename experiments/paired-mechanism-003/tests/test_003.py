from __future__ import annotations

import io
import json
import tempfile
import unittest
import urllib.error
import zipfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parents[1]
import sys
sys.path.insert(0, str(ROOT))

from api_retry import (
    GlobalRequestPacer,
    JSONTransportResult,
    ResponsesAPIError,
    RetryingJSONTransport,
    backoff_seconds,
)
from assignment import decrypt_map_in_memory, generate_assignment
from canary_binding import verify_bound_canary
from key_derivation import derive_key, new_descriptor, pop_secret_hex_from_env
from collect_execution import collect
from common import (
    API_RETRY_MAX_ATTEMPTS,
    CAPACITY_CANARY_RECEIPT_SHA256,
    EXPERIMENT_ID,
    GLOBAL_MIN_REQUEST_START_INTERVAL_SECONDS,
    PAIR_CONTROLLED_WORST_CASE_SECONDS,
    PAIR_JOB_TIMEOUT_MINUTES,
    PAIR_JOB_TIMEOUT_SECONDS,
    PAIR_MATRIX_MAX_PARALLEL,
    PINNED_MODEL,
    PUBLICATION_COMMITMENT_SHA256,
    SCORER_FREEZE_SHA256,
    SCIENTIFIC_HASHES,
    SOURCE_SCIENTIFIC_EXPERIMENT_ID,
    load_json,
    sha256_file,
)
from execute_pair import PairInfrastructureFailure, write_infrastructure_receipt
from perturbation import OneShotEligibleReadDelivery, final_quarter_truncate
from responses_agent import ResponsesClient, function_calls
from trace_format import assert_export_safe



TEST_KEY_DERIVATION_SECRET = "0123456789abcdef" * 4
TEST_KEY_CONTEXT = "terryncew/openline-lite@" + ("a" * 40) + "#123456789"


class FakeClock:
    def __init__(self):
        self.value = 0.0
        self.sleeps: list[float] = []
        self.base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    def monotonic(self): return self.value
    def sleep(self, seconds):
        seconds = float(seconds); self.sleeps.append(seconds); self.value += seconds
    def utc_now(self): return (self.base + timedelta(seconds=self.value)).isoformat()
    def advance(self, seconds): self.value += float(seconds)


class FakeResponse:
    def __init__(self, obj, *, status=200, headers=None, clock: FakeClock | None = None, active_seconds=0):
        self.status = status
        self.headers = headers or {}
        self._data = json.dumps(obj).encode()
        self.clock = clock
        self.active_seconds = active_seconds
    def __enter__(self):
        if self.clock and self.active_seconds: self.clock.advance(self.active_seconds)
        return self
    def __exit__(self, *args): return False
    def read(self): return self._data


def http_error(status: int, *, error_type="rate_limit_error", code="rate_limit_exceeded", retry_after=None):
    body = json.dumps({"error": {"message": "DO_NOT_PERSIST_THIS_MESSAGE", "type": error_type, "code": code}}).encode()
    headers = {
        "x-ratelimit-limit-tokens": "2000000",
        "x-ratelimit-remaining-tokens": "1000000",
    }
    if retry_after is not None: headers["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError("https://api.openai.com/v1/responses", status, "x", headers, io.BytesIO(body))


def transport(clock: FakeClock, opener):
    pacer = GlobalRequestPacer(
        min_interval_seconds=45,
        initial_delay_seconds=45,
        sleep_fn=clock.sleep,
        monotonic_fn=clock.monotonic,
        utc_now_fn=clock.utc_now,
    )
    return RetryingJSONTransport(urlopen_fn=opener, pacer=pacer, monotonic_fn=clock.monotonic)


class Test003(unittest.TestCase):
    def test_scientific_payload_hashes_are_byte_identical(self):
        frozen = ROOT / "frozen_scientific"
        for name, expected in SCIENTIFIC_HASHES.items():
            self.assertEqual(sha256_file(frozen / name), expected)
        pair = load_json(frozen / "PAIR_SET_FROZEN.json")
        self.assertEqual(pair["experiment_id"], SOURCE_SCIENTIFIC_EXPERIMENT_ID)
        self.assertEqual(len(pair["pairs"]), 30)
        self.assertEqual(EXPERIMENT_ID, "olp-core21-paired-mechanism-003")

    def test_bound_canary_is_exact_pass_and_not_assignment_authority(self):
        rec = verify_bound_canary(ROOT)
        self.assertEqual(rec["receipt_sha256"], CAPACITY_CANARY_RECEIPT_SHA256)
        self.assertEqual(rec["requests_completed"], 6)
        self.assertGreaterEqual(rec["minimum_observed_start_gap_seconds"], 44.99)
        self.assertFalse(rec["authorizes_assignment_by_itself"])

    def test_pacer_enforces_initial_guard_and_every_start_gap(self):
        clock = FakeClock()
        p = GlobalRequestPacer(sleep_fn=clock.sleep, monotonic_fn=clock.monotonic, utc_now_fn=clock.utc_now)
        p.wait_for_start(attempt=1)
        p.wait_for_start(attempt=1)
        p.wait_for_start(attempt=2)
        self.assertEqual(clock.sleeps, [45.0, 45.0, 45.0])
        times = [datetime.fromisoformat(e["started_at_utc"]) for e in p.start_events]
        self.assertTrue(all((b-a).total_seconds() >= 45 for a,b in zip(times,times[1:])))

    def test_transient_429_retries_but_all_attempts_remain_45s_paced(self):
        clock = FakeClock()
        seq = [http_error(429, retry_after=1), http_error(429), FakeResponse({"status":"completed","model":PINNED_MODEL,"id":"r","output":[],"usage":{}}, clock=clock)]
        def opener(req, timeout):
            item = seq.pop(0)
            if isinstance(item, Exception): raise item
            return item
        result = transport(clock, opener).request(mock.Mock(), total_timeout_seconds=100)
        self.assertEqual(result.attempts, 3)
        self.assertEqual(clock.sleeps, [45.0, 45.0, 45.0])
        self.assertEqual([e["http_status"] for e in result.retry_events], [429, 429])
        text = json.dumps(result.retry_events)
        self.assertNotIn("DO_NOT_PERSIST_THIS_MESSAGE", text)
        self.assertIn("x-ratelimit-limit-tokens", text)

    def test_quota_429_is_permanent_and_not_retried(self):
        clock = FakeClock()
        calls=[]
        def opener(req, timeout):
            calls.append(1)
            raise http_error(429, error_type="insufficient_quota", code="insufficient_quota")
        with self.assertRaises(ResponsesAPIError) as cm:
            transport(clock, opener).request(mock.Mock(), total_timeout_seconds=100)
        self.assertEqual(len(calls), 1)
        self.assertEqual(cm.exception.detail.category, "HTTP_429_QUOTA_OR_SPEND_PERMANENT")
        self.assertFalse(cm.exception.detail.retryable)
        self.assertEqual(clock.sleeps, [45.0])

    def test_503_exhausts_exact_retry_budget(self):
        clock=FakeClock(); calls=[]
        def opener(req, timeout):
            calls.append(1); raise http_error(503, error_type="server_error", code="server_error")
        with self.assertRaises(ResponsesAPIError) as cm:
            transport(clock, opener).request(mock.Mock(), total_timeout_seconds=100)
        self.assertEqual(len(calls), API_RETRY_MAX_ATTEMPTS)
        self.assertEqual(clock.sleeps, [45.0]*API_RETRY_MAX_ATTEMPTS)
        self.assertEqual(cm.exception.detail.http_status, 503)

    def test_retry_after_is_capped_even_though_45s_pacer_dominates(self):
        self.assertEqual(backoff_seconds(1, 99), 15.0)
        self.assertEqual(backoff_seconds(2, None), 4.0)

    def test_pacing_wait_is_separate_from_active_api_time(self):
        clock=FakeClock()
        def opener(req, timeout):
            return FakeResponse({"status":"completed","model":PINNED_MODEL,"id":"r","output":[],"usage":{}}, clock=clock, active_seconds=3)
        result=transport(clock, opener).request(mock.Mock(), total_timeout_seconds=10)
        self.assertEqual(result.infrastructure_wait_seconds, 45.0)
        self.assertEqual(result.active_api_seconds, 3.0)

    def test_completed_response_is_not_replayed_and_usage_is_counted(self):
        result=JSONTransportResult(
            obj={"status":"completed","model":PINNED_MODEL,"id":"r1","output":[{"type":"function_call","name":"apply_patch","call_id":"c1","arguments":"{}"}],
                 "usage":{"input_tokens":100,"output_tokens":20,"total_tokens":120,"input_tokens_details":{"cached_tokens":30}}},
            attempts=3,retry_events=[{"category":"HTTP_429_RATE_LIMIT_TRANSIENT"},{"category":"HTTP_503_SERVER_TRANSIENT"}],
            request_start_events=[{"attempt":1},{"attempt":2},{"attempt":3}],infrastructure_wait_seconds=90,active_api_seconds=2,response_headers={"x-ratelimit-limit-tokens":"2000000"})
        class T:
            def __init__(self): self.calls=0
            def request(self, req, total_timeout_seconds): self.calls+=1; return result
        t=T(); client=ResponsesClient("k", transport=t)
        response=client.create(instructions="x",history=[],timeout=10)
        self.assertEqual(t.calls,1)
        self.assertEqual(client.completed_response_count,1)
        self.assertEqual(client.input_tokens,100)
        self.assertEqual(client.output_tokens,20)
        self.assertEqual(client.cached_input_tokens,30)
        self.assertEqual(len(function_calls(response)),1)

    def test_runtime_bound_fits_300_minute_pair_job(self):
        self.assertEqual(PAIR_MATRIX_MAX_PARALLEL,1)
        self.assertEqual(PAIR_JOB_TIMEOUT_MINUTES,300)
        self.assertLess(PAIR_CONTROLLED_WORST_CASE_SECONDS,PAIR_JOB_TIMEOUT_SECONDS)
        self.assertGreaterEqual(PAIR_JOB_TIMEOUT_SECONDS-PAIR_CONTROLLED_WORST_CASE_SECONDS,3000)

    def test_execution_workflow_is_exact_tag_serial_and_no_manual_dispatch(self):
        text=(REPO_ROOT/".github/workflows/olp-30pair-003-execution.yml").read_text()
        self.assertIn('RUN_REAL_OLP_CORE21_PAIRED_MECHANISM_003',text)
        self.assertIn('max-parallel: 1',text)
        self.assertIn('timeout-minutes: 300',text)
        self.assertNotIn('workflow_dispatch',text)
        self.assertNotIn('max-parallel: 3',text)

    def test_preflight_has_no_assignment_or_live_capacity_probe(self):
        text=(REPO_ROOT/".github/workflows/olp-30pair-003-preflight.yml").read_text()
        self.assertIn('preflight/olp-core21-paired-mechanism-003',text)
        self.assertNotIn('assignment.py',text)
        self.assertNotIn('capacity_probe.py',text)
        self.assertNotIn('OPENAI_API_KEY',text)

    def test_guard_is_scoped_only_to_003(self):
        text=(ROOT/"guard_once.py").read_text()
        self.assertIn('olp-30pair-003-assignment-lock',text)
        self.assertIn('olp-30pair-003-execution.yml',text)
        self.assertNotIn('olp-30pair-002-assignment-lock',text)

    def test_disposable_assignment_is_fresh_balanced_and_003_only(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td); pub=d/'pub'; sealed=d/'sealed'
            lock=generate_assignment(
                pair_set_path=ROOT/'frozen_scientific/PAIR_SET_FROZEN.json',public_dir=pub,sealed_dir=sealed,key_derivation_secret_hex=TEST_KEY_DERIVATION_SECRET,key_context=TEST_KEY_CONTEXT,
                design_sha256=SCIENTIFIC_HASHES['BENCHMARK_DESIGN_FROZEN.json'],pair_set_sha256=SCIENTIFIC_HASHES['PAIR_SET_FROZEN.json'],
                signal_schema_sha256=SCIENTIFIC_HASHES['SIGNAL_SCHEMA_FROZEN_SCOPE_REPAIRED.json'],perturbation_sha256=SCIENTIFIC_HASHES['PERTURBATION_SPEC_FROZEN_SCOPE_REPAIRED.json'],
                preflight_pass_sha256='a'*64,runner_manifest_sha256='b'*64,publication_commitment_sha256=PUBLICATION_COMMITMENT_SHA256,scorer_freeze_sha256=SCORER_FREEZE_SHA256,dry_run=True)
            self.assertEqual(lock['pair_count'],30); self.assertEqual(lock['execution_count'],60)
            man=load_json(pub/'blinded_run_manifest.json')
            self.assertEqual(man['experiment_id'],EXPERIMENT_ID)
            self.assertEqual(len({r['opaque_execution_id'] for r in man['executions']}),60)
            self.assertFalse(lock['plaintext_key_artifact_created'])
            self.assertFalse(lock['derived_key_persisted'])
            self.assertFalse(lock['key_derivation_secret_exported'])
            self.assertFalse(any(p.name == 'secret_key.bin' for p in d.rglob('*')))
            with tempfile.TemporaryDirectory() as sx:
                import zipfile as _zf
                with _zf.ZipFile(sealed/'SEALED_CONDITION_BUNDLE.zip') as z:
                    z.extractall(sx)
                secret_map = decrypt_map_in_memory(Path(sx), TEST_KEY_DERIVATION_SECRET, TEST_KEY_CONTEXT)
                self.assertEqual(len(secret_map['conditions']), 60)

    def test_key_derivation_is_deterministic_context_bound_and_never_exported(self):
        descriptor = new_descriptor(TEST_KEY_CONTEXT)
        a = derive_key(TEST_KEY_DERIVATION_SECRET, descriptor, expected_run_context=TEST_KEY_CONTEXT)
        b = derive_key(TEST_KEY_DERIVATION_SECRET, descriptor, expected_run_context=TEST_KEY_CONTEXT)
        self.assertEqual(a, b)
        self.assertEqual(len(a), 32)
        self.assertFalse(descriptor["derived_key_persisted"])
        self.assertFalse(descriptor["plaintext_key_artifact_created"])
        with self.assertRaises(ValueError):
            derive_key(TEST_KEY_DERIVATION_SECRET, descriptor, expected_run_context=TEST_KEY_CONTEXT + "x")

    def test_secret_env_is_consumed_and_removed(self):
        with mock.patch.dict("os.environ", {"OLP_003_KEY_DERIVATION_SECRET": TEST_KEY_DERIVATION_SECRET}, clear=False):
            value = pop_secret_hex_from_env()
            self.assertEqual(value, TEST_KEY_DERIVATION_SECRET)
            import os
            self.assertNotIn("OLP_003_KEY_DERIVATION_SECRET", os.environ)

    def test_infrastructure_receipt_preserves_sanitized_detail_and_token_counts(self):
        with tempfile.TemporaryDirectory() as td:
            f=PairInfrastructureFailure('HTTP_429_RATE_LIMIT_TRANSIENT',detail={
                'failure_class':'MODEL_API','execution_phase':'BRANCH_EXECUTION',
                'api_failure':{'http_status':429,'openai_error_type':'rate_limit_error','openai_error_code':'rate_limit_exceeded','retry_after_header':'2','attempt':4,'timestamp_utc':'x'},
                'api_metrics':{'api_attempt_count':4,'completed_response_count':3,'retry_count':3,'input_tokens':1000,'output_tokens':50,'total_tokens':1050,'cached_input_tokens':0,'returned_models':[PINNED_MODEL],'failure_events':[]},
            })
            rec=write_infrastructure_receipt(Path(td),'P05',f)
            text=json.dumps(rec)
            self.assertIn('rate_limit_exceeded',text); self.assertIn('1000',text)
            self.assertNotIn('CLEAN',text); self.assertNotIn('PERTURBED',text); self.assertNotIn('api_key',text.lower())

    def test_collector_includes_infrastructure_and_usage_totals(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td); pub=d/'pub'; sealed=d/'sealed'
            generate_assignment(
                pair_set_path=ROOT/'frozen_scientific/PAIR_SET_FROZEN.json',public_dir=pub,sealed_dir=sealed,key_derivation_secret_hex=TEST_KEY_DERIVATION_SECRET,key_context=TEST_KEY_CONTEXT,
                design_sha256=SCIENTIFIC_HASHES['BENCHMARK_DESIGN_FROZEN.json'],pair_set_sha256=SCIENTIFIC_HASHES['PAIR_SET_FROZEN.json'],
                signal_schema_sha256=SCIENTIFIC_HASHES['SIGNAL_SCHEMA_FROZEN_SCOPE_REPAIRED.json'],perturbation_sha256=SCIENTIFIC_HASHES['PERTURBATION_SPEC_FROZEN_SCOPE_REPAIRED.json'],
                preflight_pass_sha256='a'*64,runner_manifest_sha256='b'*64,publication_commitment_sha256=PUBLICATION_COMMITMENT_SHA256,scorer_freeze_sha256=SCORER_FREEZE_SHA256,dry_run=True)
            pairs=d/'pairs'; pairs.mkdir()
            f=PairInfrastructureFailure('HTTP_503_SERVER_TRANSIENT',detail={'api_metrics':{'api_attempt_count':4,'completed_response_count':2,'retry_count':3,'input_tokens':1000,'output_tokens':100,'total_tokens':1100,'cached_input_tokens':50,'infrastructure_wait_seconds':90,'active_api_seconds':3,'returned_models':[PINNED_MODEL],'failure_events':[]}})
            write_infrastructure_receipt(pairs,'P05',f)
            out=d/'out'
            rec=collect(pair_artifacts=pairs,blinded_manifest=pub/'blinded_run_manifest.json',assignment_lock=pub/'ASSIGNMENT_LOCK.json',sealed_condition_zip=sealed/'SEALED_CONDITION_BUNDLE.zip',out_dir=out,runner_manifest_sha256='b'*64,preflight_pass_sha256='a'*64)
            self.assertEqual(rec['status'],'EXECUTION_INCOMPLETE_BLIND')
            self.assertEqual(rec['benchmark_api_attempt_count'],4)
            self.assertEqual(rec['benchmark_input_tokens'],1000)
            self.assertEqual(rec['benchmark_output_tokens'],100)
            with zipfile.ZipFile(out/'PUBLIC_SCORER_EXECUTION_BUNDLE.zip') as z:
                self.assertIn('infrastructure/P05.infrastructure.json',z.namelist())

    def test_perturbation_semantics_are_unchanged(self):
        text='🙂abcdefghi'; out=final_quarter_truncate(text)
        import math
        self.assertEqual(len(text)-len(out),math.ceil(len(text)/4))
        one=OneShotEligibleReadDelivery(); self.assertNotEqual(one.deliver(text,alter=True),text)
        with self.assertRaises(RuntimeError): one.deliver(text,alter=True)

    def test_public_export_rejects_condition_material(self):
        with self.assertRaises(ValueError): assert_export_safe({'x':'CLEAN'})
        with self.assertRaises(ValueError): assert_export_safe({'condition':'x'})

    def test_parent_map_has_exact_30_pairs(self):
        obj=load_json(ROOT/'PARENT_MAP_FROZEN_003.json')
        self.assertEqual(len(obj['pairs']),30)
        self.assertEqual(sorted(obj['pairs']),[f'P{i:02d}' for i in range(1,31)])

    def test_package_has_no_real_assignment_material(self):
        forbidden={'secret_key.bin','condition_map.enc','blinded_run_manifest.json','ASSIGNMENT_LOCK.json'}
        names={p.name for p in ROOT.rglob('*') if p.is_file()}
        self.assertTrue(forbidden.isdisjoint(names))

    def test_guard_ignores_predecessors_and_skipped_003_but_blocks_attempted_003(self):
        import guard_once
        def fake(url, token):
            if '/artifacts?' in url: return {'artifacts':[]}
            if '/actions/runs?' in url: return {'workflow_runs':[
                {'id':101,'path':'.github/workflows/olp-30pair-002-execution.yml','name':'OLP 30-pair 002 real execution — exact tag only'},
                {'id':202,'path':'.github/workflows/olp-30pair-003-execution.yml','name':'OLP 30-pair 003 real execution — exact tag only'}]}
            if '/runs/202/jobs' in url: return {'jobs':[{'name':'assign-once','conclusion':'skipped','id':1}]}
            raise AssertionError(url)
        with mock.patch.object(guard_once,'api_json',side_effect=fake):
            self.assertEqual(guard_once.prior_assignment_evidence('r','t','999'),[])
        def fake2(url, token):
            if '/artifacts?' in url: return {'artifacts':[]}
            if '/actions/runs?' in url: return {'workflow_runs':[{'id':202,'path':'.github/workflows/olp-30pair-003-execution.yml','name':'OLP 30-pair 003 real execution — exact tag only'}]}
            if '/runs/202/jobs' in url: return {'jobs':[{'name':'assign-once','conclusion':'failure','id':3}]}
            raise AssertionError(url)
        with mock.patch.object(guard_once,'api_json',side_effect=fake2):
            evidence=guard_once.prior_assignment_evidence('r','t','999')
        self.assertEqual(evidence[0]['kind'],'003_prior_assign_job_attempt')

    def test_dry_preflight_writes_nested_receipt_without_external_calls(self):
        import preflight_003
        with tempfile.TemporaryDirectory() as td:
            out=Path(td)/'a'/'b'/'receipt.json'
            rec,digest=preflight_003.run_preflight(out=out,perform_checkouts=False,perform_network_sandbox=False)
            self.assertTrue(out.exists())
            self.assertEqual(rec['status'],'PREFLIGHT_003_DRY_RUN_PASS')
            self.assertEqual(rec['benchmark_model_calls'],0)
            self.assertFalse(rec['real_assignment_created'])

    def test_lineage_discloses_blinded_partial_signal_inspection(self):
        obj=load_json(ROOT/'LINEAGE_001_002_ABORTS_AND_CANARY.json')
        p2=next(x for x in obj['predecessors'] if x['experiment_id'].endswith('-002'))
        self.assertTrue(p2['opaque_signal_values_inspected_after_retirement'])
        self.assertFalse(p2['condition_linked_effect_inspected'])
        self.assertIn('not claimed to be independent',p2['disclosure'])

    def test_readme_matches_actual_canary_and_no_stale_probe_claim(self):
        text=(REPO_ROOT/'README-003-PREFLIGHT.md').read_text()
        self.assertIn('14,215',text)
        self.assertIn('13,000–16,000',text)
        self.assertNotIn('18,000–40,000',text)
        self.assertNotIn('12 sequential',text)

    def test_execution_workflow_seals_blind_scores_before_key_access(self):
        workflow=(REPO_ROOT/'.github/workflows/olp-30pair-003-execution.yml').read_text()
        blind=workflow.split('  blind-score-and-capstone-gate:',1)[1].split('  independently-verify-blind-scores:',1)[0]
        verify=workflow.split('  independently-verify-blind-scores:',1)[1].split('  unblind-once-and-publish:',1)[0]
        unblind=workflow.split('  unblind-once-and-publish:',1)[1].split('  publish-blind-infrastructure-capstone:',1)[0]
        self.assertIn('blind_score.py',blind)
        self.assertNotIn('OLP_003_KEY_DERIVATION_SECRET',blind)
        self.assertIn('independent_verify_scores.py',verify)
        self.assertNotIn('OLP_003_KEY_DERIVATION_SECRET',verify)
        self.assertIn('OLP_003_KEY_DERIVATION_SECRET',unblind)
        self.assertIn('unblind_publish.py',unblind)
        self.assertNotIn('secret-key-material',workflow)
        self.assertNotIn('secret_key.bin',workflow)
        self.assertNotIn('--secret-key',workflow)
        self.assertEqual(workflow.count('${{ secrets.OLP_003_KEY_DERIVATION_SECRET }}'), 4)

    def test_protected_secret_is_validated_before_assign_job_can_start(self):
        workflow=(REPO_ROOT/'.github/workflows/olp-30pair-003-execution.yml').read_text()
        validate=workflow.split('  validate-protected-secret:',1)[1].split('  assign-once:',1)[0]
        assign=workflow.split('  assign-once:',1)[1].split('  execute-pairs:',1)[0]
        self.assertIn('OLP_003_KEY_DERIVATION_SECRET',validate)
        self.assertIn('[0-9A-Fa-f]{64}',validate)
        self.assertNotIn('assignment.py',validate)
        self.assertNotIn('--key-context',validate)
        self.assertNotIn('upload-artifact',validate)
        self.assertIn('needs: [pre_run_003, validate-protected-secret]',assign)
        self.assertIn("if: github.run_attempt == 1 && needs.pre_run_003.result == 'success' && needs.validate-protected-secret.result == 'success'",assign)
        execute=workflow.split('  execute-pairs:',1)[1].split('  collect-public:',1)[0]
        self.assertIn("if: github.run_attempt == 1 && needs.assign-once.result == 'success'",execute)

    def test_client_is_constructed_after_workspace_preparation(self):
        text=(ROOT/'execute_pair.py').read_text()
        self.assertLess(text.index('common = prepare_workspace(pair, temp_root)'), text.index('client = ResponsesClient(os.environ.get("OPENAI_API_KEY", ""))'))

    def test_package_manifest_contains_no_cache_artifacts(self):
        manifest = load_json(REPO_ROOT / "PACKAGE_MANIFEST.json")
        bad = [
            rel
            for rel in manifest["files"]
            if rel.endswith(".pyc")
            or "__pycache__/" in rel
            or ".pytest_cache/" in rel
        ]
        self.assertEqual(bad, [])

    def test_no_real_assignment_after_tests(self):
        self.assertFalse((ROOT/'build/assignment').exists())


if __name__ == '__main__': unittest.main()
