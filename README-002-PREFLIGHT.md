# OLP Core 2.1 paired mechanism benchmark — experiment 002 preflight

Experiment identity: `olp-core21-paired-mechanism-002`.

This is a fresh preregistered rerun after `olp-core21-paired-mechanism-001` terminated as a blinded infrastructure-aborted run. 001 remains unscored and unblinded. This package contains no 001 secret key, condition plaintext, condition map, or scientific score.

## Scientific payload

The four scientific artifacts are inherited byte-for-byte from 001. Their internal 001 identity is intentionally not rewritten. 002 binds their exact hashes:

- design `fd0b9eb2e2f494031bac8448dba3f6344071a4d8f2ea9285d2c3fd8ecc159f7f`
- pair set `5c622e0deaf500f7f39d9c5afece7550c1fa4859155d8b7191eb67bb0a725533`
- signal schema `88dbb498881e84e32b7599ec2ec1bf186a923bc17d7e0c67c4d09cf2b9cddb8d`
- perturbation spec `1a94515d691b86719b17885c3bf983fbe7d052affebc22406ec35b34fc9bc9e4`

No model, reasoning setting, tool/output/time budget, denominator, scoring rule, interpretation rule, task prompt, pair, perturbation, or scientific exclusion rule is changed.

## 001 diagnostic boundary

The 001 public receipt established an incomplete blinded execution, not a negative kappa result. The 001 runner collapsed underlying OpenAI/transport failures into `MODEL_API_FAILURE`, so 002 does **not** claim that 001 was caused by HTTP 429 without separate account/API telemetry. Whole-pair missingness is also not presented as evidence of condition-blind failure because the runner architecture aborts the pair job as a unit.

## Frozen 002 infrastructure regime

- GitHub matrix `max-parallel: 1`.
- Retry only transient HTTP 429 (except quota/spend exhaustion), HTTP 500/502/503/504, and narrowly classified timeout/connection/DNS transport failures.
- Four total attempts maximum per individual API request: initial + three retries.
- Deterministic backoff: 2s, 4s, 8s; a server `Retry-After` may increase a delay but is capped at 15s. No jitter.
- Authentication, malformed/permanent HTTP failures, model mismatch, quota/spend exhaustion, non-JSON completed HTTP responses, and incomplete application responses are not retried.
- A completed model response is never retried. A branch or completed pair is never restarted by retry logic.
- Sanitized infrastructure receipts preserve HTTP status, OpenAI error type/code when available, Retry-After metadata, transport category, attempt number, timestamp, and aggregate attempt/retry/completed-response counts. API keys, raw response bodies/messages, condition labels, and private assignment data are excluded.
- The conservative controlled one-pair bound is under the 60-minute GitHub matrix-job timeout. Retry/backoff time is charged inside the existing 1200-second scientific wall budget rather than added beyond it.

## Standalone 002 preflight

Create branch:

`preflight/olp-core21-paired-mechanism-002`

Extract this root-ready package into the repository root and push that branch. The push-only workflow `.github/workflows/olp-30pair-002-preflight.yml` will:

1. verify inherited scientific hashes and 002 execution manifests;
2. re-check all 30 exact parent commits;
3. verify the agent shell network namespace;
4. verify the 60-minute one-pair runtime bound;
5. make 12 sequential **non-scientific** capacity probes using exactly `gpt-5.5-2026-04-23`, medium reasoning, no benchmark task/pair content, and the frozen retry policy;
6. emit `PREFLIGHT_002_PASS.json` or fail closed.

Required repository secret: `OPENAI_API_KEY`.

The package also contains the exact future 002 execution workflow. It is inert unless the exact tag `RUN_REAL_OLP_CORE21_PAIRED_MECHANISM_002` is pushed. **Do not create or push that tag during preflight.** The real workflow repeats the exact same preflight immediately before assignment and binds that receipt hash to the fresh assignment.

## Stop condition

After the standalone preflight passes, stop and inspect its receipt before creating any 002 real trigger tag.

Build-state guarantees for this package:

`REAL_ASSIGNMENT_NOT_CREATED`

`BENCHMARK_MODEL_CALLS_0`

`UNBLINDED_FALSE`
