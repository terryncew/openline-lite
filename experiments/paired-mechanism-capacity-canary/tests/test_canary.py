from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import canary  # noqa: E402


class Resp:
    status = 200

    def __init__(self, obj, headers=None):
        self.obj = obj
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.obj).encode()


def completed_obj(input_tokens=24_000, output_text="OK"):
    return {
        "status": "completed",
        "model": canary.PINNED_MODEL,
        "output_text": output_text,
        "usage": {"input_tokens": input_tokens, "output_tokens": 1, "total_tokens": input_tokens + 1},
    }


def good(req, timeout):
    body = json.loads(req.data)
    assert body["max_output_tokens"] == 16_384
    assert body["reasoning"] == {"effort": "medium"}
    return Resp(
        completed_obj(),
        {
            "x-ratelimit-limit-tokens": "500000",
            "x-ratelimit-remaining-tokens": "410000",
            "x-ratelimit-reset-tokens": "10s",
        },
    )


class CanaryTests(unittest.TestCase):
    def test_pass_is_bounded_writes_sidecar_and_records_headers(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            sleeps = []
            t = [0.0]

            def mono():
                return t[0]

            def sleep(seconds):
                sleeps.append(seconds)
                t[0] += seconds

            out = tmp_path / "receipt.json"
            receipt = canary.run_canary(api_key="x", out=out, urlopen_fn=good, sleep_fn=sleep, monotonic_fn=mono)
            self.assertEqual(receipt["disposition"], "CAPACITY_CANARY_PASS")
            self.assertEqual(receipt["requests_started"], canary.REQUEST_COUNT)
            self.assertEqual(len(sleeps), canary.REQUEST_COUNT - 1)
            self.assertTrue(all(s == canary.MIN_INTERVAL_SECONDS for s in sleeps))
            self.assertTrue(out.exists() and out.with_suffix(".json.sha256").exists())
            self.assertFalse(receipt["policy"]["assignment_created"])
            self.assertEqual(receipt["policy"]["retries"], 0)
            self.assertEqual(receipt["rows"][0]["rate_limit_headers"]["x-ratelimit-remaining-tokens"], "410000")
            self.assertTrue(receipt["rows"][0]["output_text_exact_ok"])

    def test_payload_is_exact_size_and_representative_reservation(self):
        payload = canary.make_payload(1)
        text = payload["input"][0]["content"][0]["text"]
        synthetic = text.split("\n", 1)[1]
        self.assertEqual(len(synthetic.encode("utf-8")), canary.PAYLOAD_BYTES)
        self.assertEqual(payload["max_output_tokens"], 16_384)
        self.assertNotIn("xxxxxxxxxxxxxxxx", synthetic)

    def test_first_429_stops_without_retry_and_records_headers(self):
        with tempfile.TemporaryDirectory() as td:
            calls = [0]

            def bad(req, timeout):
                calls[0] += 1
                body = json.dumps({"error": {"type": "tokens", "code": "rate_limit_exceeded"}}).encode()
                headers = {"Retry-After": "12", "x-ratelimit-remaining-tokens": "0"}
                raise urllib.error.HTTPError(canary.API_URL, 429, "rate", headers, io.BytesIO(body))

            receipt = canary.run_canary(
                api_key="x", out=Path(td) / "r.json", urlopen_fn=bad, sleep_fn=lambda _: None, monotonic_fn=lambda: 0
            )
            self.assertEqual(receipt["disposition"], "CAPACITY_CANARY_BLOCKED")
            self.assertEqual(calls[0], 1)
            row = receipt["rows"][0]
            self.assertEqual(row["failure_category"], "HTTP_429_STOP_FIRST_FAILURE")
            self.assertEqual(row["rate_limit_headers"]["retry-after"], "12")
            self.assertEqual(row["rate_limit_headers"]["x-ratelimit-remaining-tokens"], "0")

    def test_noncompleted_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            def incomplete(req, timeout):
                return Resp({"status": "incomplete", "model": canary.PINNED_MODEL, "usage": {}})

            receipt = canary.run_canary(
                api_key="x", out=Path(td) / "r.json", urlopen_fn=incomplete, sleep_fn=lambda _: None, monotonic_fn=lambda: 0
            )
            self.assertEqual(receipt["disposition"], "CAPACITY_CANARY_BLOCKED")
            self.assertEqual(receipt["requests_started"], 1)
            self.assertEqual(receipt["rows"][0]["failure_category"], "NON_COMPLETED_OR_MODEL_MISMATCH")

    def test_input_token_range_mismatch_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            def too_small(req, timeout):
                return Resp(completed_obj(input_tokens=5_000))

            receipt = canary.run_canary(
                api_key="x", out=Path(td) / "r.json", urlopen_fn=too_small, sleep_fn=lambda _: None, monotonic_fn=lambda: 0
            )
            self.assertEqual(receipt["disposition"], "CAPACITY_CANARY_BLOCKED")
            self.assertEqual(receipt["rows"][0]["failure_category"], "INPUT_TOKEN_RANGE_MISMATCH")

    def test_unexpected_output_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            def verbose(req, timeout):
                return Resp(completed_obj(output_text="OK, here is more"))

            receipt = canary.run_canary(
                api_key="x", out=Path(td) / "r.json", urlopen_fn=verbose, sleep_fn=lambda _: None, monotonic_fn=lambda: 0
            )
            self.assertEqual(receipt["disposition"], "CAPACITY_CANARY_BLOCKED")
            self.assertEqual(receipt["rows"][0]["failure_category"], "UNEXPECTED_OUTPUT_ENVELOPE")

    def test_workflow_uses_exact_tag_only(self):
        workflow = (ROOT.parents[1] / ".github" / "workflows" / "olp-low-cost-capacity-canary.yml").read_text("utf-8")
        self.assertNotIn("workflow_dispatch", workflow)
        self.assertIn("RUN_LOW_COST_CAPACITY_CANARY_ONLY", workflow)
        self.assertIn("tags:", workflow)


if __name__ == "__main__":
    unittest.main()
