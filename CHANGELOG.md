# Changelog

## 0.3.1 — 2026-07-17

- Replaced recursive canonical-value validation with an explicit iterative stack.
- Added fixed canonical JSON limits of 128 levels and 100,000 nodes.
- Converted parser recursion failures into `CanonicalJSONError` so hostile input fails closed instead of escaping as `RecursionError`.
- Added regression tests for `loads`, `EvidenceGateway`, claim support, `ReceiptGate`, native chain verification, and the mapped adapter.
- Restricted JSON Pointer array indexes to ASCII digits and rejected oversized indexes before integer conversion.
- Expanded the suite from 32 to 44 tests.

Security: v0.3.0 is superseded. A deeply nested unauthenticated JSON artifact could crash every ingestion path before signature verification.

## 0.3.0 — 2026-07-17

- Added bounded native receipt-chain verification with parent, sequence, issuer, run, size, and trust checks.
- Added `olp.handoff.v1` full projections and compact JSONL prompt handoffs.
- Required valid pinned gate decisions and exact receiver policy-hash allowlists before facts can enter a handoff.
- Excluded sources with conflicting eligible non-commit decisions.
- Kept arbitrary source-authored claims out of the prompt projection while retaining them in the full audit object.
- Added `verify-chain`, `handoff`, and `benchmark` CLI commands.
- Added a three-track benchmark with explicit break-even reporting and nine hostile disposition fixtures.
- Added manifest, decision, fact, claim, chain, and aggregate byte limits.
- Added a handoff schema, conformance case, runnable example, adoption guide, benchmark methodology, and CI benchmark artifact.

The benchmark reports token cost, stored bytes, latency, allocation, and decision correctness separately. It does not claim improved LLM output quality.

## 0.2.0 — 2026-07-17

- Added receiver-owned bounded claim-support replay.
- Added the perfectly signed but unsupported hostile control.
- Added a declarative adapter for Ed25519-signed canonical JSON receipts.
- Separated normalization from cryptographic integrity and provenance.
- Added source-size protection, packaged schemas, conformance runner, clean-wheel CI, and adopter documentation.
- Expanded decision verification to cover the new assessment set and gate-ID binding.

Policy files from 0.1.0 must add `claim_rules`. An empty list is intentionally undecidable.

## 0.1.0 — 2026-07-17

- Initial native source receipt, Evidence Gateway, Receipt Gate, five dispositions, and receiver decision verification.
