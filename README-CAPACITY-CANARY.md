# Low-cost capacity canary — calibrated retry 1

This package does **not** create experiment 003, an assignment, a condition map, a benchmark trace, or a score. It is a second and final execution of the same bounded capacity diagnostic after the first canary stopped on an overly narrow local validation rule.

The first live request returned HTTP 200, status `completed`, exact output `OK`, and provider-reported input usage of **14,215 tokens**. It did not receive a 429. The old canary stopped only because its frozen range incorrectly required 18,000–40,000 input tokens.

This repair changes only that calibration and the one-shot trigger identity:

- accepted observed input-token range: **13,000–16,000**
- exact new trigger tag: `RUN_LOW_COST_CAPACITY_CANARY_ONLY_RETRY1`

The request envelope remains unchanged: at most six Responses API requests, pinned model `gpt-5.5-2026-04-23`, medium reasoning, deterministic 80,000-byte synthetic context, `max_output_tokens: 16384`, expected output exactly `OK`, starts at least 45 seconds apart, zero retries, and stop on first failure. Sanitized rate-limit headers and provider usage are recorded.

## Install without running

Extract this ZIP into the repository root and push the branch. A branch push does not run the canary. Let ordinary CI settle.

Do **not** reuse or move the historical tag `RUN_LOW_COST_CAPACITY_CANARY_ONLY`. When ready, create and push exactly:

`RUN_LOW_COST_CAPACITY_CANARY_ONLY_RETRY1`

Do not rerun either tag without reading the resulting receipt.

Interpretation:

- `CAPACITY_CANARY_PASS`: this bounded six-call envelope survived. It still does not authorize a full benchmark or experiment 003.
- `CAPACITY_CANARY_BLOCKED`: stop and do not spend on 003 at this envelope.

Exact dollar cost is provider-controlled. Request count and usage are bounded and recorded.
