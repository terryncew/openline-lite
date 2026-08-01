# Low-cost capacity canary

This package does **not** create experiment 003, an assignment, a condition map, a benchmark trace, or a score. It adds one exact-tag capacity discriminator.

The canary makes at most six Responses API requests to the pinned model, starts requests at least 45 seconds apart, allows no retries, and stops on the first failure. Each request carries a deterministic 80,000-byte synthetic context, requests the benchmark-matching `max_output_tokens: 16384` admission envelope, and instructs the model to return exactly `OK`.

The runtime receipt records provider-reported input/output tokens and sanitized rate-limit headers. A successful response must report between 18,000 and 40,000 input tokens; otherwise the canary fails closed because the request was not representative of the frozen envelope.

## Install without running

Extract this ZIP into the repository root and push the branch. A branch push does not run the canary.

Let ordinary CI settle. Then, once you deliberately choose to spend on the diagnostic, create and push the exact tag:

`RUN_LOW_COST_CAPACITY_CANARY_ONLY`

Do not create that tag merely to test installation. Do not rerun the canary without first reading its receipt.

Interpretation:

- `CAPACITY_CANARY_PASS`: this bounded representative envelope survived. It still does **not** authorize or clear a full 30-pair run.
- `CAPACITY_CANARY_BLOCKED`: stop. Do not spend on 003 at this envelope.

Exact dollar cost cannot be guaranteed because provider pricing and cached-token treatment are external. The request count is capped, the actual usage is recorded, and expected output is only `OK`.
