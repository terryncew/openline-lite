# OLP Core 2.1 paired mechanism benchmark — experiment 002 RETRY1 infrastructure repair

Experiment identity remains `olp-core21-paired-mechanism-002`.

This package is an **execution-infrastructure-only repair** after the first 002 real-trigger run stopped inside its immediate pre-assignment capacity probe. That run created no 002 assignment, made no benchmark model call, and remained unblinded. Its sealed failure receipt is preserved byte-for-byte at:

`experiments/paired-mechanism-002/RETRY1_PRIOR_PREASSIGNMENT_FAILURE.json`

SHA-256:

`3fdbe1621dda0b3aa7dc8f3f46db3cf0bc08449bfb9aace1fd69aa8fe42e641b`

The consumed trigger `RUN_REAL_OLP_CORE21_PAIRED_MECHANISM_002` must not be moved, deleted, or reused.

## Scientific payload — unchanged

The four scientific artifacts remain byte-for-byte identical to experiment 001 and to the original 002 package. Their hashes are unchanged:

- design `fd0b9eb2e2f494031bac8448dba3f6344071a4d8f2ea9285d2c3fd8ecc159f7f`
- pair set `5c622e0deaf500f7f39d9c5afece7550c1fa4859155d8b7191eb67bb0a725533`
- signal schema `88dbb498881e84e32b7599ec2ec1bf186a923bc17d7e0c67c4d09cf2b9cddb8d`
- perturbation spec `1a94515d691b86719b17885c3bf983fbe7d052affebc22406ec35b34fc9bc9e4`

No model, reasoning effort, tool/output/time budget for benchmark executions, denominator, scoring rule, interpretation rule, task prompt, pair, perturbation, or scientific exclusion rule is changed.

## What failed before assignment

The first 002 real-trigger run reached the immediate pre-assignment sustained-capacity probe and returned an application-level response that was not `completed`. The old probe then sealed only `CAPACITY_RESPONSE_NOT_COMPLETED`; it did **not** retain the returned response status, `incomplete_details.reason`, sanitized usage, or returned-model metadata. Therefore this package does not retroactively claim the exact reason for that response.

The old probe also set `max_output_tokens: 16` while asking the pinned reasoning model to run at medium reasoning effort. RETRY1 removes that known fragility by using the benchmark's already-frozen execution ceiling, `16384`, for the non-scientific probe. This does not change the benchmark execution configuration; it makes the capacity probe use the same output-token ceiling it is supposed to validate.

RETRY1 also records strictly sanitized application-response diagnostics before failing closed:

- response `status`;
- `incomplete_details.reason` when present;
- returned model identifier;
- SHA-256 of the response ID, never the raw ID;
- numeric token-usage fields only;
- request output-token ceiling;
- API attempt/retry counts and already-sanitized retry events.

Raw model output, response messages, API keys, benchmark pair content, condition labels, and private assignment material are never persisted by this diagnostic path.

## Retry behavior and execution regime — unchanged

- GitHub matrix `max-parallel: 1`.
- Retry only transient HTTP 429 (except quota/spend exhaustion), HTTP 500/502/503/504, and narrowly classified timeout/connection/DNS transport failures.
- Four total attempts maximum per individual API request: initial + three retries.
- Deterministic backoff: 2s, 4s, 8s; `Retry-After` may increase a delay but is capped at 15s. No jitter.
- Authentication, malformed/permanent HTTP failures, model mismatch, quota/spend exhaustion, non-JSON HTTP success responses, and incomplete application responses are not retried.
- A completed model response is never retried. A branch or completed pair is never restarted by retry logic.
- Retry/backoff time remains charged inside the existing scientific wall budget.
- The conservative one-pair bound remains below the 60-minute matrix-job timeout.

## RETRY1 trigger and guard

The real execution workflow now listens only for:

`RUN_REAL_OLP_CORE21_PAIRED_MECHANISM_002_RETRY1`

The original 002 tag is no longer an accepted trigger in the repaired workflow.

The assignment guard remains fail-closed. It ignores a prior 002 `assign-once` job only when that job was `skipped`; any prior non-skipped 002 assignment attempt or surviving 002 assignment-lock artifact blocks RETRY1.

## Required sequence

1. Extract this root-ready package into the repository root on `preflight/olp-core21-paired-mechanism-002`.
2. Commit and push the repaired runner files. Do not create RETRY1 yet.
3. Let `.github/workflows/olp-30pair-002-preflight.yml` run and inspect the new sealed preflight receipt.
4. Only if that receipt is `PREFLIGHT_002_PASS`, with `real_assignment_created=false`, `benchmark_model_calls=0`, and `unblinded=false`, create the exact RETRY1 tag on the repaired commit.
5. Push the RETRY1 tag once. Do not rerun a real execution workflow and do not move/reuse either real-run tag.

Required repository secret: `OPENAI_API_KEY`.

## Build-state guarantees

`REAL_ASSIGNMENT_NOT_CREATED`

`BENCHMARK_MODEL_CALLS_0`

`UNBLINDED_FALSE`
