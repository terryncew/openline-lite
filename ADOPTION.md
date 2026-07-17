# Adoption guide

OpenLine Lite is useful when an agent workflow has several steps, the next model does not need every prior token, and you still want the carryover facts to pass receiver-owned checks.

It is a poor fit for a one-shot prompt, a workflow with no retrievable evidence, or a system that needs formal semantic proof rather than bounded JSON facts.

## Fifteen-minute integration path

1. Start with one consequential action type such as `tool_call` or `memory_write`.
2. Preserve the exact tool-result bytes locally.
3. Issue an `olp.source.v1` receipt containing the evidence SHA-256.
4. Pin the producer public key outside the receipt.
5. Write a small receiver policy with only the facts the next step needs.
6. Run `ReceiptGate.decide` and retain its signed decision beside the source receipt.
7. At a model boundary, verify the complete chain and build a projection with the exact allowed policy hashes.
8. Put the JSONL projection—not the entire retained evidence bundle—into the next prompt.
9. Run the benchmark on representative traces and choose `max_claims` from measured task requirements.

## Minimum production wrapper

The reference package uses local raw private-key files in CLI examples. Replace that boundary before production:

- use a platform key service or isolated signer;
- bind key IDs to an externally managed trust store;
- retain original receipt and evidence bytes under your own retention policy;
- make the gate clock explicit and monitored;
- cap source, evidence, chain, decision, claim, and output sizes;
- treat `QUARANTINE` as a real workflow with an owner and timeout;
- keep rollback execution outside OpenLine Lite and record whether it succeeded;
- log the exact package, policy, profile, and schema versions.

## Choosing the fact budget

The default prompt projection carries the latest three accepted items. That is a starting point, not a recommendation for every workload.

Measure at least:

- downstream task success;
- prompt input tokens;
- output tokens;
- end-to-end latency;
- gate disposition correctness;
- undecidable rate;
- evidence retrieval failures;
- bytes retained outside the prompt.

Do not combine these into an invented single score. A smaller prompt that damages downstream task success is not a win.

## Adapter admission

OpenLine Lite's generic mapped adapter is not a blanket compatibility claim. Before naming an external format, add a version-pinned profile and fixtures for:

- valid signature and pinned signer;
- invalid signature;
- embedded but untrusted signer;
- missing required fields;
- evidence hash mismatch;
- producer-supplied `verified` or `safe` fields;
- canonicalization edge cases;
- the exact trust boundary of the external format.

Open an adapter proposal only with public sample artifacts or a reproducible generator. The receiver must remain the owner of trust, policy, and disposition.

## Best first use case

Use OpenLine Lite between two local or hosted agents in a deterministic tool workflow. Keep the full run on disk, carry only verified facts into the next prompt, and compare the result with your existing full-history implementation. That yields a cost result and a task-quality result without requiring a platform rewrite.
