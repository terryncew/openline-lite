from __future__ import annotations

import hashlib
import json
from pathlib import Path

EXPERIMENT_ID = "olp-core21-paired-mechanism-001"
BENCHMARK_REVISION = "RESEALED_AFTER_SCOPE_REPAIR"
GREEN_PREFLIGHT_COMMIT = "54d906cce8354bd58d1fd664a5028c4e0ec1f0be"
PINNED_MODEL = "gpt-5.5-2026-04-23"
REASONING_EFFORT = "medium"
PREFLIGHT_PASS_SHA256 = "3b2aa2ca991b82a343ac7a4bdc953947f4db45f07bf072925da07c02182b6d98"

FROZEN_HASHES = {
    "BENCHMARK_DESIGN_FROZEN.json": "fd0b9eb2e2f494031bac8448dba3f6344071a4d8f2ea9285d2c3fd8ecc159f7f",
    "PAIR_SET_FROZEN.json": "5c622e0deaf500f7f39d9c5afece7550c1fa4859155d8b7191eb67bb0a725533",
    "SIGNAL_SCHEMA_FROZEN_SCOPE_REPAIRED.json": "88dbb498881e84e32b7599ec2ec1bf186a923bc17d7e0c67c4d09cf2b9cddb8d",
    "PERTURBATION_SPEC_FROZEN_SCOPE_REPAIRED.json": "1a94515d691b86719b17885c3bf983fbe7d052affebc22406ec35b34fc9bc9e4",
    "PREFLIGHT_BLOCKED_SCOPE_MISMATCH.json": "7383db129c7121a06350eb19ca83e40080d17811a64f362dd2c2c04b1d6aaa9b",
    "PREFLIGHT_BLOCKED_RUNNER_NETWORK.json": "34bdd220ff865421b3d1ba3c014274ae553e037288e1d2ec3619713187d78e68",
    "RESEALED_AFTER_SCOPE_REPAIR.json": "050e8e643d04af4c0c18158d084110ea2b001a33ea57d8d5d22bd32e95501564",
    "PREFLIGHT_PASS.json": PREFLIGHT_PASS_SHA256,
}

ALLOWED_TOOLS = ("read_file", "list_tree", "search_text", "apply_patch", "run_shell")
MAX_OUTPUT_TOKENS = 16384
MAX_TOOL_CALLS = 40
MAX_WALL_SECONDS = 1200
SHELL_TIMEOUT_SECONDS = 120
MAX_COMMON_PREFIX_TOOL_CALLS = 20
MIN_ELIGIBLE_READ_CODEPOINTS = 1024

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
