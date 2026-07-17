# Architecture

OpenLine Lite separates the carrier from the decision.

## Evidence Gateway

The gateway owns intake. It hashes the exact source bytes, selects a versioned adapter, recomputes format-specific integrity, checks external pinned trust, and produces a normalized payload plus three independent assessments: integrity, provenance, and normalization.

The native adapter verifies `olp.source.v1`. The mapped adapter supports a receiver-supplied field profile for Ed25519-signed canonical JSON. A producer's own `verified`, `safe`, or `approved` field has no authority.

## Receipt Gate

The gate owns the receiver decision. It evaluates action policy and freshness, recomputes required evidence hashes, and evaluates explicit claim rules against locally supplied JSON evidence. It then applies a fail-closed disposition table:

| Assessment set | Verdict | Disposition |
|---|---|---|
| Every check passes | VERIFIED | COMMIT |
| At least one check fails | REJECTED | DENY, or ROLLBACK_REQUEST when locally enabled and an effect is asserted |
| No failures and at least one unavailable | UNDECIDABLE | QUARANTINE or NO_BADGE |

The complete policy snapshot, its canonical hash, all assessments, reason codes, source-byte hash, and disposition are placed in the receiver-signed decision receipt.

## Native chain verification

`verify_native_chain` accepts a bounded ordered list of native source receipts. Every receipt must pass integrity, pinned provenance, and normalization. The chain starts at sequence zero with no parent; each later item must keep the same run and issuer, increment the sequence, and name the deterministic envelope hash of the prior item.

Size and count limits fail closed. A self-consistent but untrusted chain remains unavailable and cannot produce a handoff.

## Verified handoff projection

`build_handoff_projection` joins a verified native chain with receiver-signed decisions. Admission requires:

- a pinned gate key and valid decision signature;
- a source hash belonging to this exact chain;
- the native source format;
- an exact receiver-approved policy hash;
- a `VERIFIED / COMMIT` result with no conflicting eligible non-commit decision.

The full `olp.handoff.v1` object preserves hashes, excluded counts, source claims, and receiver-selected support facts. The compact JSONL prompt view carries only action names and receiver-selected facts. Arbitrary source-authored prose remains outside the prompt because bounded equality rules do not prove general natural-language entailment.

The prompt header contains a prefix of the chain tip and a digest of the allowed-policy set. These prefixes are compact context markers, not independent security proofs. Security decisions must be made by the verifier against the retained full receipts and trust stores before prompt construction.

## Wire

The reference envelope uses strict deterministic JSON and Ed25519. Floats and duplicate JSON keys are rejected. Validation uses an explicit stack with fixed limits of 128 levels and 100,000 values, so deeply nested input fails closed without Python recursion. The profile is deliberately smaller than a general canonicalization standard; compatibility must be claimed only for byte-for-byte tested profiles.

## Decision verification

Independent verification checks the receiver signature and pinned key, validates the policy hash, reconstructs reason codes from assessments, and recomputes the disposition. It establishes internal consistency of the signed decision receipt. Full evidence replay additionally requires the original source receipt, adapter profile, evidence bytes, and evaluation time.

## Extension boundary

New receipt formats belong in gateway adapters. New evidence appraisers belong before the disposition function and must return pass, fail, or unavailable with stable reason codes. Neither extension point may silently convert missing information into a pass.

The benchmark is outside the authorization path. It measures prompt carryover, local verification work, stored bytes, and fixture decisions independently; it cannot grant authority or replace conformance tests.
