from __future__ import annotations

import hashlib
import json
from pathlib import Path

EXPERIMENT_ID = "olp-core21-paired-mechanism-003"
SOURCE_SCIENTIFIC_EXPERIMENT_ID = "olp-core21-paired-mechanism-001"
PREDECESSOR_EXPERIMENT_IDS = ("olp-core21-paired-mechanism-001", "olp-core21-paired-mechanism-002")
BENCHMARK_REVISION = "FRESH_45S_PACED_RERUN_AFTER_001_002_BLINDED_INFRASTRUCTURE_ABORTS"
PINNED_MODEL = "gpt-5.5-2026-04-23"
REASONING_EFFORT = "medium"

# Scientific payload is inherited byte-for-byte from 001. Its internal identity remains
# 001 intentionally; 003 binds these exact bytes rather than rewriting the science.
SCIENTIFIC_HASHES = {
    "BENCHMARK_DESIGN_FROZEN.json": "fd0b9eb2e2f494031bac8448dba3f6344071a4d8f2ea9285d2c3fd8ecc159f7f",
    "PAIR_SET_FROZEN.json": "5c622e0deaf500f7f39d9c5afece7550c1fa4859155d8b7191eb67bb0a725533",
    "SIGNAL_SCHEMA_FROZEN_SCOPE_REPAIRED.json": "88dbb498881e84e32b7599ec2ec1bf186a923bc17d7e0c67c4d09cf2b9cddb8d",
    "PERTURBATION_SPEC_FROZEN_SCOPE_REPAIRED.json": "1a94515d691b86719b17885c3bf983fbe7d052affebc22406ec35b34fc9bc9e4",
}
PARENT_MAP_SHA256 = "a9e5d3f81b08ad395688848027688cdd389de8990cb28a6a45ee85793d8542c1"
LINEAGE_SHA256 = "4757fbfb2e002c55945697a8db99b66b012d3d18034b1d5176e933c060de1cd4"
CAPACITY_CANARY_RECEIPT_SHA256 = "964397e3dd3f3030844b1947da86141eeabef4883f4a0d450dae65488700df77"
PUBLICATION_COMMITMENT_SHA256 = "e90ea4e48eb76e350a3ddc2888e2d164f8b29634b7473a65a3a798c1fda02283"
SCORER_FREEZE_SHA256 = "9f3843685a016f891d59f63abe3c7f8954dfa2bc0ce5c4bd5bb84b8d959870e9"

ALLOWED_TOOLS = ("read_file", "list_tree", "search_text", "apply_patch", "run_shell")
MAX_OUTPUT_TOKENS = 16384
MAX_TOOL_CALLS = 40
MAX_WALL_SECONDS = 1200
SHELL_TIMEOUT_SECONDS = 120
MAX_COMMON_PREFIX_TOOL_CALLS = 20
MIN_ELIGIBLE_READ_CODEPOINTS = 1024

# Execution-infrastructure-only 003 policy.
PAIR_MATRIX_MAX_PARALLEL = 1
GLOBAL_MIN_REQUEST_START_INTERVAL_SECONDS = 45
PAIR_JOB_INITIAL_PACING_GUARD_SECONDS = 45
API_RETRY_MAX_ATTEMPTS = 4
API_RETRY_BACKOFF_SECONDS = (2, 4, 8)
API_RETRY_AFTER_CAP_SECONDS = 15
RETRYABLE_HTTP_STATUSES = (429, 500, 502, 503, 504)
PERMANENT_429_CODES = (
    "insufficient_quota", "billing_hard_limit_reached", "billing_not_active",
    "usage_limit_reached", "spend_limit_reached",
)
PERMANENT_429_TYPES = ("insufficient_quota", "billing_error")
CHECKPOINT_FETCH_TIMEOUT_SECONDS = 180
CHECKPOINT_CHECKOUT_TIMEOUT_SECONDS = 60
PAIR_JOB_INFRASTRUCTURE_ALLOWANCE_SECONDS = 600
PAIR_JOB_TIMEOUT_SECONDS = 18000
PAIR_JOB_TIMEOUT_MINUTES = 300

# Conservative structural bound. At most 63 response cycles can be requested across
# the common prefix and both branches. Every cycle can consume all four API attempts.
PAIR_MAX_RESPONSE_CYCLES = MAX_COMMON_PREFIX_TOOL_CALLS + 2 * (MAX_TOOL_CALLS - MAX_COMMON_PREFIX_TOOL_CALLS) + 3
PAIR_MAX_API_REQUEST_STARTS = PAIR_MAX_RESPONSE_CYCLES * API_RETRY_MAX_ATTEMPTS
PAIR_MAX_PACING_WAIT_SECONDS = PAIR_MAX_API_REQUEST_STARTS * GLOBAL_MIN_REQUEST_START_INTERVAL_SECONDS
PAIR_CONTROLLED_WORST_CASE_SECONDS = (
    2 * MAX_WALL_SECONDS
    + CHECKPOINT_FETCH_TIMEOUT_SECONDS
    + CHECKPOINT_CHECKOUT_TIMEOUT_SECONDS
    + PAIR_JOB_INFRASTRUCTURE_ALLOWANCE_SECONDS
    + PAIR_MAX_PACING_WAIT_SECONDS
)

FORBIDDEN_EXPORT_KEYS = {
    "condition", "conditions", "clean", "perturbed", "perturbation_applied",
    "original_text", "returned_text", "original_length", "returned_length",
    "truncation_fraction", "assignment", "assignment_bookkeeping", "aes_key",
    "decryption_key", "condition_map", "condition_map_plaintext",
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
