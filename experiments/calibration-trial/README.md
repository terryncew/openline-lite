# OpenLine Lite — Calibration Trial

This is one prospective experiment, not a new product layer.

It asks one question: **do frozen, receiver-owned Coherence Dynamics measurements predict whether a human must correct a dropped or wrong decision, fact, requirement, constraint, file state, or policy within the next eight assistant messages better than simpler preregistered baselines?**

The design is intentionally hard to rescue after the fact. The protocol is frozen before held-out scoring. Thresholds are fit only on older labeled handoffs. Every prospective handoff is ledgered before scoring. Exactly the first 20 eligible handoffs form the test set. All 20 predictions must be sealed before the software will unlock prospective outcome labeling. Evaluation then compares Δhol against four frozen baselines using balanced accuracy plus a preregistered exact paired randomization test and 95% paired effect interval.

The included synthetic fixture is mechanism-only. It proves that the machinery closes its declared leakage paths; it is **not evidence that Coherence Dynamics predicts real handoff failure**.

## What is frozen before case #1

`protocol.yaml` owns all of the following:

- binary outcome definition and eight-assistant-message window;
- exact κ / ε / Δhol / Φ* / VKD computation profile;
- Δhol claim/evidence/relation weights;
- calibration threshold fitting and tie-break rules;
- four comparators: always-safe, session length, tool-call count, and fixed keyword heuristic;
- fixed 20 calibration + 20 prospective sample sizes and class floors;
- 600-second eligibility and prediction deadlines;
- exact inference rule: one-sided paired randomization at α=0.05 against **every** comparator;
- 95% exactly enumerated stratified bootstrap percentile interval for each paired balanced-accuracy difference.

The measurement profile belongs to the receiver/protocol. A session may submit observations only. Session-supplied metric weights or profiles are rejected.

## Prospective sequence

Run from `experiments/calibration-trial/`.

```bash
# 1. Freeze the exact protocol bytes before any real held-out case.
python -m calibration_trial preregister \
  protocol.yaml \
  --out build/preregistration.json

# 2. Fit thresholds only from the 20 selected pre-freeze handoffs.
python -m calibration_trial freeze \
  protocol.yaml \
  --sessions data/calibration \
  --continuations continuations/calibration \
  --outcomes labels/calibration.jsonl \
  --out build/freeze.json

# 3. For every new trial handoff: register, then score, both <=600 s.
python -m calibration_trial register \
  protocol.yaml build/freeze.json data/test/session-031.json \
  --eligibility-ledger build/eligibility.jsonl

python -m calibration_trial score \
  protocol.yaml build/freeze.json data/test/session-031.json \
  --eligibility-ledger build/eligibility.jsonl \
  --predictions build/predictions.jsonl
```

Do that for exactly the first 20 eligible post-freeze handoffs. Do **not** formally review or write prospective labels yet.

```bash
# 4. Only after prediction #20 is sealed, unlock outcome labeling once.
python -m calibration_trial unlock-outcomes \
  protocol.yaml build/freeze.json \
  --eligibility-ledger build/eligibility.jsonl \
  --predictions build/predictions.jsonl \
  --out build/outcome-unlock.json

# 5. Review continuation transcripts and write labels after the unlock.
python -m calibration_trial label \
  protocol.yaml build/freeze.json \
  --eligibility-ledger build/eligibility.jsonl \
  --predictions build/predictions.jsonl \
  --outcome-unlock build/outcome-unlock.json \
  --continuation continuations/test/session-031.json \
  --session-id session-031 \
  --outcome 1 \
  --kind constraint \
  --correction-message-index 3 \
  --notes "Dropped frozen API constraint." \
  --out labels/test.jsonl

# 6. Evaluate once after all 20 labels are complete.
python -m calibration_trial evaluate \
  protocol.yaml build/freeze.json \
  --sessions data/test \
  --eligibility-ledger build/eligibility.jsonl \
  --continuations continuations/test \
  --predictions build/predictions.jsonl \
  --outcome-unlock build/outcome-unlock.json \
  --outcomes labels/test.jsonl \
  --out build/evaluation.json
```

Then run the independent implementation against the same raw artifacts:

```bash
python -m calibration_trial.independent_verify \
  protocol.yaml build/freeze.json \
  --calibration-sessions data/calibration \
  --calibration-continuations continuations/calibration \
  --calibration-outcomes labels/calibration.jsonl \
  --test-sessions data/test \
  --eligibility-ledger build/eligibility.jsonl \
  --test-continuations continuations/test \
  --predictions build/predictions.jsonl \
  --outcome-unlock build/outcome-unlock.json \
  --test-outcomes labels/test.jsonl \
  --evaluation build/evaluation.json
```

## Primary result rule

A positive pilot result requires all of these at the fixed N=20 endpoint:

1. the preregistered class floor is met;
2. Δhol held-out balanced accuracy is strictly greater than **every** comparator;
3. the one-sided exact paired randomization p-value is ≤0.05 against **every** comparator.

The evaluation also reports a 95% paired balanced-accuracy-difference interval against every comparator. The interval is descriptive uncertainty; the preregistered inferential gate is the exact paired test. κ, Φ*, and VKD are secondary and cannot rescue a failed primary Δhol result.

If either prospective class has fewer than the preregistered minimum of 3 observations, evaluation emits `INSUFFICIENT_SAMPLE`; paired inference is recorded as `not_run` rather than attempted. Calibration continuations must start strictly after their associated handoff, so impossible pre-handoff continuations cannot influence threshold fitting.

The “better than all” claim is an intersection-union gate: every component comparison must clear the same preregistered α. There is no post-hoc choice of the easiest baseline.

## Leakage boundary

The software closes the obvious retrospective escape hatches:

- no random held-out split;
- no threshold changes after protocol freeze;
- no producer-controlled metric profile/weights;
- no skipped or replaced ledgered handoff;
- no scoring after the 600-second window;
- no prediction after continuation start;
- no formal prospective label before all 20 predictions are sealed;
- no early evaluation or extension past N=20;
- independent recomputation must reproduce metrics, thresholds, predictions, inference, and evaluation.

One boundary remains physical rather than magical: this is a **label-data blackout, not double-blinding**. If the same human participates in the continuation, they may personally notice that a correction occurred before label review. The protection is that the scoring rule, sample, thresholds, and predictions are already frozen and hash-bound, and the software will not accept prospective labels before the all-predictions unlock.

For stronger ordering/completeness evidence than a local filesystem can provide, preserve eligibility-chain tail hashes and the final outcome-unlock hash in git commits or OpenLine receipts as the trial runs.

## Claim boundary

Before real prospective data are complete, the valid claim is:

> The Calibration Trial preregisters a temporal test of receiver-owned Coherence Dynamics measurements, fixes the first 20 eligible prospective handoffs before outcome labeling, and independently reproduces the paired statistical evaluation from raw admitted inputs.

It does **not** establish that Δhol, κ, Φ*, or VKD predict real handoff failure until genuine prospective held-out results exist and survive the preregistered gate.

Release verification writes its runtime report to `build/RELEASE_VERIFICATION.json`. That generated report is intentionally outside the sealed source manifest, so rerunning the gate cannot dirty the shipped manifest.
