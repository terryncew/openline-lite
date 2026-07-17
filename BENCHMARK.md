# Benchmark: verified handoff cost and decision quality

The benchmark tests one narrow claim:

> After a workload-specific break-even point, a bounded verified handoff can reduce prompt carryover while preserving receiver-side policy decisions.

It reports prompt tokens, bytes, CPU time, wall time, peak Python allocation, stored receipt bytes, stored evidence bytes, and disposition correctness as separate measurements. `combined_score` is always `null`.

## Three matched cost tracks

For each synthetic workflow depth, the benchmark builds the same deterministic run and measures:

1. **Full history:** every plan, tool call, tool result, and assistant result is placed in the next prompt.
2. **Unsigned compact:** the same bounded action and receiver-selected fact projection is placed in the next prompt without verification metadata.
3. **Verified handoff:** the bounded projection is produced only after native chain verification, pinned producer trust, pinned gate trust, signed decision verification, and exact policy-hash allowlisting.

The unsigned control matters. It exposes how much of the savings comes from selection and compression rather than cryptography. The verified track should cost more than the unsigned compact track; the difference is reported as verification metadata overhead.

One evidence item is checked when each receiver decision is issued. The later chain-verification and projection phase trusts those pinned, receiver-signed decisions and performs zero additional evidence reads. The benchmark claims no reduction in the original evidence work; full independent replay still needs the retained evidence bundle.

The JSONL prompt projection intentionally excludes arbitrary source-authored prose. The full audit projection retains `source_claim`, but prompt carryover contains only receiver-selected policy facts. OpenLine Lite's equality rules do not prove unrestricted natural-language entailment.

## One handoff and cumulative handoffs

- `one_handoff` asks what it costs to hand the complete run to one next model.
- `cumulative_at_every_handoff` asks what it costs when a handoff occurs after every step.

`break_even` reports the first **tested** depth where the verified track uses no more prompt tokens than full history. It does not interpolate between depths and must not be treated as a universal threshold.

## Decision-quality fixture

Nine declared cases compare the full gate with a signature-only baseline:

| Case | Expected disposition |
|---|---|
| Valid receipt, matching evidence and policy | `COMMIT` |
| Required evidence missing | `QUARANTINE` |
| Signed evidence contradicts the required fact | `DENY` |
| Payload changed after signing | `DENY` |
| Supplied evidence bytes miss the committed hash | `DENY` |
| Action is disallowed by policy | `DENY` |
| Receipt is expired | `QUARANTINE` |
| Self-consistent signer is not pinned | `QUARANTINE` |
| Receipt timestamp is in the future | `DENY` |

The baseline checks source integrity, provenance, and normalization, then commits every all-pass receipt. This is intentionally narrow: it represents signature-only admission, not a named competitor or a state-of-the-art external system.

## Reproduce

Dependency-free lexical count:

```bash
olp-lite benchmark \
  --depths 1,2,4,8,16,32 \
  --iterations 20 \
  --tokenizer lexical \
  --out benchmark-lexical.json
```

Reference tokenizer:

```bash
pip install '.[benchmark]'
olp-lite benchmark \
  --depths 1,2,4,8,16,32 \
  --iterations 50 \
  --tokenizer tiktoken:cl100k_base \
  --out benchmark-cl100k.json
```

`iterations` affects timing samples, not deterministic token counts or fixture decisions. The committed reference file is [benchmarks/results/reference-cl100k.json](benchmarks/results/reference-cl100k.json).

## Reference interpretation

With `cl100k_base`, the committed fixture crosses its first tested one-handoff break-even at depth 2 and cumulative break-even at depth 4. At depth 16 the verified projection uses 85.8% fewer tokens for one handoff and 74.3% fewer cumulatively than full history.

The policy fixture is 9/9 for OpenLine Lite and 3/9 for the signature-only baseline. Cost reduction and decision correctness remain separate findings.

## What this benchmark does not establish

- It does not call an LLM or measure answer quality, task completion, or hallucination rate.
- It does not model provider pricing, cache discounts, output tokens, or tool latency.
- It does not compare named receipt protocols.
- It does not prove complete capture, semantic truth, or production safety.
- It uses synthetic traces, deterministic fixture keys, and one local Python process.
- It does not show that three retained facts are enough for every downstream task.
- Timing results are environment-specific and should not be compared across machines without controls.

## Falsifiers

The cost claim fails on a target workload if verified handoffs do not reach a useful break-even after evidence availability and retention are matched.

The decision claim fails if a cheaper method reaches the same or better correct dispositions on the same hostile cases with the same evidence and trust inputs.

The practical adoption claim fails if independent developers cannot reproduce the result or if downstream task quality falls because the selected fact budget is too small. That requires a separate task-quality benchmark; this repository does not pretend the current fixture answers it.
