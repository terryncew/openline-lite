# Release verification

OpenLine Lite v0.3.1 was verified locally on Python 3.12.13 before packaging.

| Check | Result |
|---|---|
| Ruff format and lint | Pass |
| Unit and integration tests | 44/44 pass |
| Depth-2000 hostile JSON across all ingestion paths | Fails closed |
| Unicode and oversized JSON Pointer indexes | Rejected without crash |
| Conformance checkpoint | Pass, including verified chain handoff |
| Runnable JSONL handoff example | Pass |
| Source distribution and wheel build | Pass |
| Clean-wheel install, `pip check`, demo, and lexical benchmark | Pass |
| Five packaged JSON schemas | Present and parseable |
| Cache, key-file, unfinished-marker, and network-client scan | Pass |

Reference benchmark SHA-256:

```text
d6e96412173119c655cc62b4edfce3261a8fbc72a11eac974ec02e0484153d31  benchmarks/results/reference-cl100k.json
```

The committed `cl100k_base` result uses depths 1, 2, 4, 8, 16, and 32, 50 timing iterations per depth, and a three-item prompt budget. It records a first tested one-handoff break-even at depth 2 and cumulative break-even at depth 4. At depth 16, prompt-token reductions are 85.8% for one handoff and 74.3% cumulatively.

The nine-case policy fixture is 9/9 for OpenLine Lite and 3/9 for the narrow signature-only baseline. It measures disposition correctness, not LLM answer quality.

v0.3.1 supersedes v0.3.0. Canonical validation is iterative and rejects documents above 128 levels or 100,000 values. The regression suite confirms normal failure behavior in `loads`, `EvidenceGateway`, claim support, `ReceiptGate`, native chain verification, and the mapped adapter.

Machine-readable detail and limitations are in [EVIDENCE.json](EVIDENCE.json). This evidence is author-run; no external reproduction is claimed.
