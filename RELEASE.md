# GitHub release kit

Repository: `openline-lite`

Description:

> Verified, bounded AI-agent handoffs that cut prompt carryover after break-even without trusting signatures alone.

Topics: `ai-agents`, `agent-security`, `audit`, `receipts`, `ed25519`, `evidence`, `handoff`, `prompt-engineering`, `python`, `zero-trust`

## v0.3.1 release title

`OpenLine Lite v0.3.1 — bounded verification, including hostile JSON depth`

## Release body

OpenLine Lite keeps complete receipt chains and evidence locally while carrying only receiver-selected facts into the next model prompt.

This release includes native chain verification, exact policy-hash admission, pinned signed gate decisions, conflict exclusion, and a bounded JSONL handoff. Arbitrary source-authored prose remains in the full audit projection but stays out of prompt carryover.

v0.3.1 also replaces recursive canonical JSON validation with an explicit stack capped at 128 levels and 100,000 values. Deep unauthenticated input now returns a normal canonical failure across the gateway, claim-support evaluator, receipt gate, native chain verifier, and mapped adapter. JSON Pointer array indexes are ASCII-only and bounded before integer conversion. v0.3.0 is superseded.

The included three-track benchmark compares full history, the same unsigned compact projection, and the verified handoff. With `cl100k_base` on the committed synthetic fixture:

- depth 1 costs 27.7% more prompt tokens than full history;
- the first tested one-handoff break-even is depth 2;
- the first tested cumulative break-even is depth 4;
- depth 16 uses 85.8% fewer one-handoff tokens and 74.3% fewer cumulative tokens;
- the policy fixture reaches 9/9 correct dispositions versus 3/9 for a narrow signature-only baseline.

Run it:

```bash
pip install .
olp-lite demo
olp-lite benchmark --depths 1,2,4,8,16,32
python -m examples.handoff
```

The benchmark does not call an LLM or claim improved answer quality. Its token threshold is specific to the declared workload, tokenizer, and three-item fact budget. Cost, latency, stored bytes, and decision correctness remain separate.

Status: alpha reference implementation. No server, network fetcher, hardware key integration, transparency log, UCR, Δhol, named external protocol compatibility, or executed rollback is claimed.

Independent reproductions and hostile fixtures are invited.

## Suggested launch post

> Agent chains waste tokens when every handoff replays the whole run. I built OpenLine Lite to keep the receipts locally and pass only receiver-verified facts forward. The benchmark found the honest boundary: one-step runs cost more; this fixture breaks even at depth 2 and reaches 85.8% fewer handoff tokens at depth 16. Full method and falsifiers are in the repo.

Put the repository link in the first reply if the platform favors link-free primary posts.
