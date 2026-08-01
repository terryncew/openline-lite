# OLP Core 2.1 paired mechanism benchmark — experiment 003 preflight

Experiment identity: `olp-core21-paired-mechanism-003`.

This is a fresh, 45-second-paced blinded rerun after experiments 001 and 002 terminated as infrastructure-aborted blind runs. It does not reuse either predecessor assignment, condition map, key, partial trace, or score.

## Scientific payload

The scientific artifacts are byte-identical to the original 001 freeze. Their internal 001 identity is intentionally preserved:

- design `fd0b9eb2e2f494031bac8448dba3f6344071a4d8f2ea9285d2c3fd8ecc159f7f`
- pair set `5c622e0deaf500f7f39d9c5afece7550c1fa4859155d8b7191eb67bb0a725533`
- signal schema `88dbb498881e84e32b7599ec2ec1bf186a923bc17d7e0c67c4d09cf2b9cddb8d`
- perturbation spec `1a94515d691b86719b17885c3bf983fbe7d052affebc22406ec35b34fc9bc9e4`

No pair, task prompt, perturbation, model snapshot, reasoning setting, tool/output budget, denominator, scoring rule, interpretation rule, or scientific exclusion rule is changed.

## Honest lineage disclosure

001 remains unscored and unblinded.

002 remains unblinded and incomplete. After it was retired, condition-blind opaque κ values from its 14 surviving traces were inspected. No CLEAN/PERTURBED mapping, directional pair result, or condition-linked effect was available. Therefore 003 must not be described as independent of all prior signal-distribution information. It does remain a fresh assignment with no condition-linked predecessor result carried forward.

## Bound capacity evidence

The package contains the exact successful capacity receipt with SHA-256:

`964397e3dd3f3030844b1947da86141eeabef4883f4a0d450dae65488700df77`

It records six of six successful calls using `gpt-5.5-2026-04-23`, medium reasoning, `max_output_tokens: 16384`, at least 45 seconds between starts, and exactly 14,215 input tokens per call. Its calibrated policy range is **13,000–16,000** input tokens. The 003 preflight verifies this receipt; it does not repeat the canary or make any live model call.

## Frozen 003 infrastructure regime

- Matrix `max-parallel: 1`.
- Every API attempt, including retries, passes through one scheduler within the pair job.
- Each pair waits 45 seconds after setup before its first request; every later request start is at least 45 seconds after the previous start.
- Four attempts maximum per logical request: initial plus three retries.
- Retry only transient HTTP 429 except quota/spend exhaustion, HTTP 500/502/503/504, and narrow transient transport failures.
- Deterministic backoff is 2, 4, and 8 seconds. `Retry-After` may increase a delay but is capped at 15 seconds. The 45-second scheduler remains the controlling minimum.
- Authentication, malformed/permanent errors, quota/spend exhaustion, model mismatch, and incomplete application responses are not retried.
- Completed responses, tool effects, branches, and pairs are never restarted by retry logic.
- Scheduler/retry waiting is recorded separately from the inherited 1,200-second active model/tool budget. It does not create additional model/tool opportunity.
- The conservative pair bound is 14,580 seconds against a 18,000-second/300-minute matrix-job timeout, leaving 3,420 seconds of controlled margin.
- Public receipts include sanitized API failure classes, status/code, Retry-After and rate-limit headers, request-start timestamps, attempts, completed responses, retries, and token usage. They exclude keys, response bodies, condition labels, and private assignment material.


## Protected key boundary repair

The earlier 003 preflight receipt was authentic, but it did not authorize execution. The earlier workflow would have uploaded `secret_key.bin` as an Actions artifact in this public repository. The real tag was never pushed, so no assignment, key, spend, or unblinding occurred. `KEY_BOUNDARY_REPAIR_003.json` records that finding and the clean state.

The repaired workflow uses `HKDF-SHA256-32-V1`. Assignment, each pair execution, and final unblinding derive the same 256-bit AES key in memory from:

- a protected GitHub Actions secret named `OLP_003_KEY_DERIVATION_SECRET`;
- a fresh 256-bit public salt stored with the ciphertext; and
- the exact repository, tag commit SHA, and GitHub run ID.

The protected secret is absent from collection, blind scoring, and independent verification. Ciphertext, commitments, public salt, and the public context binding may be artifacts. The secret and derived key may not be artifacts.

Before the real execution tag, create the repository Actions secret:

1. Generate 32 random bytes as 64 hexadecimal characters. One safe command is `python -c "import secrets; print(secrets.token_hex(32))"`.
2. In GitHub, open **Settings → Secrets and variables → Actions → New repository secret**.
3. Name it exactly `OLP_003_KEY_DERIVATION_SECRET`.
4. Paste the generated 64-character value once. Do not commit it, paste it into chat, place it in a file, or reuse the OpenAI API key.

The branch preflight verifies the code boundary but does not receive the secret. In the exact-tag workflow, a separate `validate-protected-secret` job checks only that the repository secret exists, is exactly 64 hexadecimal characters, and is nonzero. `assign-once` cannot start unless that validation job passes. A missing or malformed secret therefore leaves the assignment job skipped rather than recording an attempted assignment. Assignment and pair execution also have job-level `github.run_attempt == 1` gates, so rerunning the workflow cannot create a second assignment or replay pair execution.

## Final publish-regardless capstone lock

Experiment 003 is now frozen as the final attempt at this exact 30-pair design. `PUBLICATION_COMMITMENT_003.json` requires publication whether the primary result is directional, chance-level, adverse, tied, not evaluable because of mechanically invalid pairs, or infrastructure-aborted before a complete scoreable bundle exists.

The result path is sealed before assignment:

1. Execution produces a condition-blind public bundle.
2. A keyless blind scorer requires the complete execution receipt. Incomplete execution creates a final blind infrastructure disposition; partial traces are not scored or stitched.
3. On a complete execution, it seals 60 blind score records and one aggregate.
4. A separate keyless verifier independently recomputes all 60 records.
5. Only after those artifacts exist does the one-time unblinding job derive the AES key in memory from a protected GitHub secret plus the run-bound public context, verify the original condition-map commitment, compute the frozen paired κ result, and create `FINAL_CAPSTONE_PUBLICATION_BUNDLE.zip`.

No plaintext condition key is ever written, uploaded, cached, placed in a matrix, or emitted as a job output. The blind scorer and independent verifier do not receive the protected derivation secret. The unblinding job cannot run unless both earlier gates pass. No favorable result is guaranteed. What is mechanically required is an honest final disposition and publication artifact.

The inherited design names `delta_hol` as secondary but provides no exact operational transform for this frozen trace schema. `SCORER_FREEZE_003.json` therefore declares it unavailable before assignment rather than inventing a post hoc formula. It cannot rescue κ.

Scorer freeze SHA-256: `47839b052d496461e5a613cf268f9d4e85c41ea948ed9c7d95b528a9939e2e6e`.

## Capstone pipeline verification

The shipped pipeline is not a placeholder. It has a complete synthetic end-to-end test that creates a disposable fresh assignment, builds a 60-trace condition-blind public bundle, seals 60 score records, independently recomputes every signal component and κ point, verifies the plaintext condition-map commitment, joins all 30 pairs, and seals the final publication bundle. The release suite passes **42/42 tests**. A separate 10,000-case randomized differential check found **0 mismatches** between the primary scorer and the independently written verifier. No live API call or real assignment was created during verification.

## Install the preflight without running 003

Create and check out:

`preflight/olp-core21-paired-mechanism-003`

Extract this ZIP into the repository root with no wrapper folder.

Commit message:

`Repair protected key boundary for experiment 003`

Push the branch. The push-only workflow `.github/workflows/olp-30pair-003-preflight.yml` verifies the frozen bytes, bound canary receipt, runner manifest, all 30 exact Git parents, network isolation, and runtime bound. It creates no assignment and makes no model calls.

Configure the protected secret before the real tag. After the new preflight passes, stop and inspect its receipt. Do not create the real tag until the receipt and secret configuration are both verified.

The future exact execution tag is:

`RUN_REAL_OLP_CORE21_PAIRED_MECHANISM_003`

That tag creates a fresh one-time assignment and begins paid benchmark execution. It must not be pushed during preflight. Do not use GitHub’s rerun controls on the real workflow; assignment and pair execution are intentionally first-attempt-only.

## Credit planning

See `COST-PLAN-003.json`. The central planning estimate is about $222, with substantial uncertainty from output tokens and context growth. Have **$350 available** before the real tag. I would not risk the run with less than $250. The comfortable planning ceiling is $450.

Build state:

`REAL_ASSIGNMENT_NOT_CREATED`

`BENCHMARK_MODEL_CALLS_0`

`UNBLINDED_FALSE`

`PUBLISH_REGARDLESS_CAPSTONE_FROZEN`

`PLAINTEXT_KEY_ARTIFACT_CREATED_FALSE`

`PROTECTED_KEY_DERIVATION_BOUNDARY_FROZEN`
