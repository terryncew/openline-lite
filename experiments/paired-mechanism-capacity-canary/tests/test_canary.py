from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import canary  # noqa: E402


class Resp:
    status = 200
    def __init__(self, obj): self.obj = obj
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return json.dumps(self.obj).encode()


def good(req, timeout):
    body = json.loads(req.data)
    return Resp({"status": "completed", "model": canary.PINNED_MODEL, "usage": {"input_tokens": 20000, "output_tokens": 5, "total_tokens": 20005}})


def test_pass_is_bounded_and_writes_sidecar(tmp_path):
    sleeps=[]; t=[0.0]
    def mono(): return t[0]
    def sleep(s): sleeps.append(s); t[0]+=s
    out=tmp_path/'receipt.json'
    r=canary.run_canary(api_key='x', out=out, urlopen_fn=good, sleep_fn=sleep, monotonic_fn=mono)
    assert r['disposition']=='CAPACITY_CANARY_PASS'
    assert r['requests_started']==canary.REQUEST_COUNT
    assert len(sleeps)==canary.REQUEST_COUNT-1
    assert out.exists() and out.with_suffix('.json.sha256').exists()
    assert r['policy']['assignment_created'] is False
    assert r['policy']['retries']==0


def test_first_429_stops_without_retry(tmp_path):
    calls=[0]
    def bad(req, timeout):
        calls[0]+=1
        body=json.dumps({'error':{'type':'tokens','code':'rate_limit_exceeded'}}).encode()
        raise urllib.error.HTTPError(canary.API_URL,429,'rate',{},io.BytesIO(body))
    r=canary.run_canary(api_key='x', out=tmp_path/'r.json', urlopen_fn=bad, sleep_fn=lambda _:None, monotonic_fn=lambda:0)
    assert r['disposition']=='CAPACITY_CANARY_BLOCKED'
    assert calls[0]==1
    assert r['rows'][0]['failure_category']=='HTTP_429_STOP_FIRST_FAILURE'


def test_noncompleted_blocks(tmp_path):
    def incomplete(req, timeout): return Resp({'status':'incomplete','model':canary.PINNED_MODEL,'usage':{}})
    r=canary.run_canary(api_key='x', out=tmp_path/'r.json', urlopen_fn=incomplete, sleep_fn=lambda _:None, monotonic_fn=lambda:0)
    assert r['disposition']=='CAPACITY_CANARY_BLOCKED'
    assert r['requests_started']==1
