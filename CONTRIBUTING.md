# Contributing

Keep changes small, receiver-owned, and falsifiable.

Before proposing a change:

```bash
python -m unittest discover -s tests -v
python -m conformance.run
olp-lite benchmark --depths 1,2,4,8 --iterations 3
```

Adapters must identify an exact source-format version and include fixtures for valid signature, invalid signature, untrusted key, missing field, and producer-supplied trust-flag attacks. Appraisers must define pass, fail, and unavailable behavior. New positive authority requires a receiver-controlled policy input and an adversarial test.

Do not describe a request as an executed rollback, an internally consistent receipt as semantic truth, or a mapped format as compatible with a named protocol without version-pinned fixtures.

Handoff changes must test an invalid chain, an untrusted signer, a decision outside the chain, a disallowed policy hash, a conflicting non-commit decision, source-text line injection, and resource limits. Source-authored prose must not be silently upgraded into a receiver-verified fact.

Benchmark changes must preserve the full-history, unsigned-compact, and verified-handoff tracks. Keep prompt tokens, bytes, latency, allocation, evidence work, and decision correctness separate. Never optimize a combined score, hide a negative result, or publish a token-saving claim without the tokenizer, depths, fact budget, and break-even rule.

Independent benchmark reproductions are welcome. Include the commit, Python version, tokenizer, command, raw JSON output, workload description, and any modifications.
