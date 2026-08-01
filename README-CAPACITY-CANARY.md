# Low-cost capacity canary

This package does **not** create experiment 003, an assignment, a condition map, or a benchmark score. It adds one manually triggered capacity discriminator.

The canary makes at most six Responses API requests to the pinned model, starts requests at least 45 seconds apart, allows no retries, and stops on the first failure. Each request carries a deterministic 80,000-byte synthetic context and permits at most 256 output tokens. This bounds tokens and requests; it cannot guarantee an exact dollar charge because provider pricing and cached-token treatment are external.

Run only from GitHub Actions using `workflow_dispatch` and enter:

`RUN_LOW_COST_CANARY_ONLY`

Interpretation:

- `CAPACITY_CANARY_PASS`: this envelope survived. It does **not** clear a full 30-pair run.
- `CAPACITY_CANARY_BLOCKED`: stop. Do not spend on 003 at this envelope.

No retry, rerun, or second canary should occur without inspecting the first receipt.
