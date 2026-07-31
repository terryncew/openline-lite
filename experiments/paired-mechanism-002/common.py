from __future__ import annotations

import hashlib
import json
from pathlib import Path

EXPERIMENT_ID = "olp-core21-paired-mechanism-002"
SOURCE_EXPERIMENT_ID = "olp-core21-paired-mechanism-001"
BENCHMARK_REVISION = "FRESH_RERUN_AFTER_001_INFRASTRUCTURE_ABORT"
PINNED_MODEL = "gpt-5.5-2026-04-23"
REASONING_EFFORT = "medium"

# Scientific payload is inherited byte-for-byte from 001. Its internal experiment_id
# remains 001 intentionally; 002 binds these exact bytes rather than rewriting them.
SCIENTIFIC_HASHES = {
    "BENCHMARK_DESIGN_FROZEN.json": "fd0b9eb2e2f494031bac8448dba3f6344071a4d8f2ea9285d2c3fd8ecc159f7f",
    "PAIR_SET_FROZEN.json": "5c622e0deaf500f7f39d9c5afece7550c1fa4859155d8b7191eb67bb0a725533",
    "SIGNAL_SCHEMA_FROZEN_SCOPE_REPAIRED.json": "88dbb498881e84e32b7599ec2ec1bf186a923bc17d7e0c67c4d09cf2b9cddb8d",
    "PERTURBATION_SPEC_FROZEN_SCOPE_REPAIRED.json": "1a94515d691b86719b17885c3bf983fbe7d052affebc22406ec35b34fc9bc9e4",
}
PARENT_MAP_SHA256 = "47e971adeaeed613a2a81c6ac2c14004003ca844c32ecf0ff7751222140e194d"
LINEAGE_SHA256 = "168a8b68209b48099e112cba2595117d88d0fdc28992dcc36eca0552a55e022a"

ALLOWED_TOOLS = ("read_file", "list_tree", "search_text", "apply_patch", "run_shell")
MAX_OUTPUT_TOKENS = 16384
MAX_TOOL_CALLS = 40
MAX_WALL_SECONDS = 1200
SHELL_TIMEOUT_SECONDS = 120
MAX_COMMON_PREFIX_TOOL_CALLS = 20
MIN_ELIGIBLE_READ_CODEPOINTS = 1024

# Execution-infrastructure-only 002 policy. No scientific budget changes.
PAIR_MATRIX_MAX_PARALLEL = 1
API_RETRY_MAX_ATTEMPTS = 4  # initial request + at most three retries
API_RETRY_BACKOFF_SECONDS = (2, 4, 8)
API_RETRY_AFTER_CAP_SECONDS = 15
RETRYABLE_HTTP_STATUSES = (429, 500, 502, 503, 504)
PERMANENT_429_CODES = (
    "insufficient_quota",
    "billing_hard_limit_reached",
    "billing_not_active",
    "usage_limit_reached",
    "spend_limit_reached",
)
PERMANENT_429_TYPES = ("insufficient_quota", "billing_error")
CAPACITY_PROBE_COUNT = 12
CAPACITY_PROBE_INTERVAL_SECONDS = 1
CHECKPOINT_FETCH_TIMEOUT_SECONDS = 180
CHECKPOINT_CHECKOUT_TIMEOUT_SECONDS = 60
PAIR_JOB_TIMEOUT_SECONDS = 3600
PAIR_JOB_INFRASTRUCTURE_ALLOWANCE_SECONDS = 600
# Common prefix is shared; the two branches each inherit the remaining scientific
# wall budget. A conservative upper bound is 2*MAX_WALL_SECONDS, plus exact-parent
# fetch/checkout and a 10-minute CI/setup allowance. Retry sleeps are inside the
# scientific wall budget because the requester receives the same monotonic deadline.
PAIR_CONTROLLED_WORST_CASE_SECONDS = (
    2 * MAX_WALL_SECONDS
    + CHECKPOINT_FETCH_TIMEOUT_SECONDS
    + CHECKPOINT_CHECKOUT_TIMEOUT_SECONDS
    + PAIR_JOB_INFRASTRUCTURE_ALLOWANCE_SECONDS
)

FORBIDDEN_EXPORT_KEYS = {
    "condition",
    "conditions",
    "clean",
    "perturbed",
    "perturbation_applied",
    "original_text",
    "returned_text",
    "original_length",
    "returned_length",
    "truncation_fraction",
    "assignment",
    "assignment_bookkeeping",
    "aes_key",
    "decryption_key",
    "condition_map",
    "condition_map_plaintext",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_json_bytes(obj) -> bytes:
    return (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def pretty_json_bytes(obj) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def load_json(path: Path):
    return json.loads(path.read_text("utf-8"))
