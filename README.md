# OpenLine Lite

[![CI](https://github.com/terryncew/openline-lite/actions/workflows/ci.yml/badge.svg)](https://github.com/terryncew/openline-lite/actions/workflows/ci.yml)
[![Python 3.10–3.13](https://img.shields.io/badge/python-3.10%E2%80%933.13-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

## New to OpenLine? [Start here](START_HERE.md)

Run a local example, see what OpenLine can do today, and choose the right path for coding agents, provider changes, or guarded tool calls.

**This repository is the front door.** [The Start Here guide](START_HERE.md) connects Lite, Airlock, Wallet, and Receipt Gate without requiring you to install the whole stack.

## What Lite does

OpenLine Lite checks evidence under receiver-owned rules and identifies earlier decisions that must reopen when their required evidence loses standing. It also provides a [verified model handoff wrapper](MODEL_HANDOFF.md) for carrying checked project facts forward.

No server or database is required. A producer's `verified` flag is not trusted.

## Three commands

| Command | Question |
|---|---|
| `openline-check` | May this action proceed under my receiver policy? |
| `openline-impact` | Which standing decisions must reopen after evidence is invalidated? |
| `olp-lite` | Verify chains, create bounded handoffs, and run the benchmark. |

## OpenLine Impact

`openline-impact` is the smallest operational form of the trust-boundary result:

```text
signed COMMIT decision
        +
exact signed source receipt
        +
receiver-required evidence bindings
        ↓
EvidenceHash → DecisionIDs
        ↓
evidence invalidated
        ↓
REOPEN / RETAIN / UNDETERMINED
```

- `REOPEN`: the invalidated evidence is in the decision's frozen required-evidence binding.
- `RETAIN`: no invalidated evidence is bound **and** the receiver declared the binding complete.
- `UNDETERMINED`: the binding is known to be incomplete. Missingness never silently becomes independence.

The command verifies the receiver-signed decision receipt, verifies the producer source receipt against externally pinned producer trust, checks the exact source-byte hash, and indexes only evidence required by the frozen receiver policy.

It does **not** discover the upstream break, infer missing dependencies, roll anything back, or authorize execution. Receiver policy decides what to do with `REOPEN` and `UNDETERMINED`.

Run the in-process example:

```bash
python -m examples.impact
```

Expected shape:

```json
{
  "REOPEN": ["deploy-api"],
  "RETAIN": ["publish-benchmark"],
  "UNDETERMINED": ["publish-docs"],
  "runtime_permission": "NONE"
}
```

For the file-based CLI pack format:

```bash
openline-impact impact-pack.json --output-dir .openline-impact
```

See [TRUST_BOUNDARY.md](TRUST_BOUNDARY.md).

### Why the flat index exists

OpenLine Lite still includes richer graph-based Selective Reverification when provenance paths matter. `openline-impact` is the flattened critical-path view for the common case where decision-specific evidence closure was already frozen at sign-off.

That simplification is deliberate. In PSD-001, an equivalent flat decision-closure index matched evidence-graph traversal exactly on the complete trial set. The earned result is about **decision-specific evidence binding**, not unique superiority of graph traversal.

The canonical external research receipt is preserved in `terryncew/openline-ace` under `results/psd001-uv-external-001/`.

## What PSD-001 earned

On a pinned external `astral-sh/uv` workspace, 30 receiver decisions were frozen before blind post-freeze dependency invalidations were selected.

Across the 24 complete intervention trials:

- decision-specific OpenLine binding: recall `1.000`, precision `1.000`, false-reopen rate `0.000`;
- artifact/component join: recall `1.000`, precision about `0.415`, false-reopen rate about `0.254`;
- repository-scope join: recall `1.000`, precision about `0.229`, false-reopen rate about `0.607`;
- equivalent decision-specific flat index: exact parity with OpenLine;
- known missing-edge arm: zero silent false retains.

This was a controlled prospective perturbation on a real external software substrate, not a natural security incident. It supports selective localization of lost standing on that frozen substrate. It does not establish early warning, causal discovery, autonomous repair, or generalization to every domain.

Canonical PSD-001 receipt SHA-256:

```text
0ac12393c6de8587e3879c67e51c02a7bd19646fe3be567b58aeb3338988078b
```

## OpenLine Check

If you only want the current receiver decision:

```bash
openline-check .openline/check.json
```

The command runs the Evidence Gateway and Receipt Gate, prints a proof card, and preserves the signed decision receipt. `COMMIT` exits successfully; `QUARANTINE`, `DENY`, `NO_BADGE`, and `ROLLBACK_REQUEST` fail closed.

The repository is also a GitHub Action:

```yaml
- uses: actions/checkout@v4
- uses: terryncew/openline-lite@v0.6.0
  with:
    check-pack: .openline/check.json
    gate-id: repo-ci
    gate-key: ${{ secrets.OPENLINE_GATE_PRIVATE_KEY }}
```

See [OPENLINE_CHECK.md](OPENLINE_CHECK.md).

## Selective Reverification

A check pack can optionally declare claim dependencies, changed roots, required claims, and claim-to-evidence bindings. OpenLine follows descendants from changed roots and withholds only evidence bound to reopened required claims before the existing Receipt Gate runs.

```text
tests-standing      REOPEN
review-standing     RETAIN

evidence withheld:
tests

Receipt Gate:
QUARANTINE
```

This is not dependency discovery or a second authority layer. The receiver owns the declarations; Receipt Gate still owns the signed disposition. See [SELECTIVE_REVERIFICATION.md](SELECTIVE_REVERIFICATION.md).

## Living-wiki standing

My living wiki compounds. What happens when an old source changes?

LLM-maintained wikis compile raw sources (`raw/`) into persistent pages
(`wiki/`). The useful property: knowledge compounds instead of being
reconstructed from scratch. The failure mode: a compiled page can keep
influencing later work after a source that justified it changed,
disappeared, or was superseded.

`openline-wiki` records which source versions each compiled page was
declared to depend on, then deterministically flags the page for
reconsideration when those recorded sources change or disappear. It does
not judge whether a wiki claim is true — it knows which pages lost the
source standing they were recorded against.

```bash
git clone https://github.com/terryncew/openline-lite
cd openline-lite && pip install .
cd my-wiki
openline-wiki init
openline-wiki record topic.md --source raw/paper-a.md --complete
openline-wiki scan
```

```text
REOPEN        topic.md (source_changed:raw/paper-a.md)
RETAIN        glossary.md
UNDETERMINED  notes.md (incomplete_capture)
```

Short version: your wiki remembers when its sources stopped matching.
REOPEN means reconsider the page; RETAIN means its recorded sources are
byte-identical and the dependency declaration was marked complete;
UNDETERMINED means standing cannot be established — treat it as
unresolved, never as current. For agent workflows see
[integrations/living-wiki/SKILL.md](integrations/living-wiki/SKILL.md).

## Install and run

```bash
python -m venv .venv
. .venv/bin/activate
pip install .

openline-check --help
openline-impact --help
openline-wiki --help
olp-lite demo
python -m examples.impact
```

For model-token benchmark counts:

```bash
pip install '.[benchmark]'
olp-lite benchmark \
  --depths 1,2,4,8,16,32 \
  --iterations 20 \
  --tokenizer tiktoken:cl100k_base \
  --out benchmark.json
```

## Existing handoff result

OpenLine Lite keeps complete receipts and evidence outside the model prompt and carries a bounded receiver-verified JSONL projection forward.

The committed reference fixture found:

| Tested depth | One next handoff | Cumulative handoff at every step |
|---:|---:|---:|
| 1 | 27.7% more tokens | 27.7% more tokens |
| 2 | 10.2% fewer tokens | 2.8% more tokens |
| 4 | 43.7% fewer tokens | 24.8% fewer tokens |
| 8 | 71.8% fewer tokens | 54.0% fewer tokens |
| 16 | 85.8% fewer tokens | 74.3% fewer tokens |
| 32 | 92.9% fewer tokens | 86.3% fewer tokens |

Those break-even points are workload-specific, not universal constants. The separate nine-case policy fixture produced 9/9 correct OpenLine Lite dispositions versus 3/9 for a narrow signature-only baseline. It measures receiver disposition correctness, not LLM answer quality.

See [BENCHMARK.md](BENCHMARK.md).

## Receiver-owned boundary

OpenLine Lite proves that the described local checks ran and that the receiver signed the resulting disposition. It does not prove:

- complete event or dependency capture;
- issuer honesty or semantic truth;
- that receiver policy chose the right facts;
- improved LLM answer quality;
- universal token savings;
- early warning or failure prediction;
- causal discovery or autonomous repair;
- hardware-backed key custody;
- transparency-log inclusion;
- side-effect reversal;
- production safety.

`ROLLBACK_REQUEST` asks another component to attempt reversal. `openline-impact` returns a standing partition. Neither executes remediation.

The included raw key workflow is for local development, not production key management.

See [ARCHITECTURE.md](ARCHITECTURE.md), [SECURITY.md](SECURITY.md), [ADOPTION.md](ADOPTION.md), [TRUST_BOUNDARY.md](TRUST_BOUNDARY.md), and [VERIFICATION.md](VERIFICATION.md).

MIT licensed. Alpha reference implementation; independent reproduction and security review are welcome.
