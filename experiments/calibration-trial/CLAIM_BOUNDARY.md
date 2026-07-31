# Claim boundary

## What this build establishes

- The protocol preregisters a prospective temporal handoff trial with a fixed 20+20 design.
- The complete COLE measurement profile, including Δhol weights, is receiver-owned and protocol-bound; sessions may submit observations only.
- Historical labels fit thresholds without access to future test labels.
- Every prospective handoff is time-bounded, ordered, hash-chained, and bound to exact session bytes before scoring.
- Scoring cannot skip an earlier eligible handoff, happen after 600 seconds, or occur after continuation start.
- Exactly 20 prospective predictions must be sealed before the write-once outcome-unlock receipt can exist.
- Prospective labels bind that unlock receipt and a separate continuation transcript.
- Evaluation requires exact alignment across sessions, eligibility, predictions, continuations, unlock, and outcomes.
- The primary inferential rule is frozen: Δhol must strictly beat every baseline and pass a one-sided exact paired randomization test at α=0.05 against every baseline.
- A 95% paired balanced-accuracy-difference interval is reported for every comparator using an exactly enumerated stratified bootstrap percentile distribution.
- The independent verifier imports no candidate scorer code and independently recomputes receiver-owned metrics, threshold fitting, prediction receipts, hash chains, outcome blackout, inference, and evaluation.

## What this build does not establish

- That any Coherence Dynamics metric predicts real handoff failure.
- That the synthetic fixture is empirical evidence. It is mechanism-only.
- That the graph or signal extractor feeding the admitted observations is itself valid.
- That a local clock, filesystem, eligibility file, or unlock file is an external timestamp/notary authority.
- That software can detect a real-world handoff deliberately hidden from both the trial and every external receipt.
- That the human participant is psychologically blinded to corrections. The enforced boundary is formal label-data blackout until all 20 predictions are sealed.
- A universal threshold, causal relationship, safety guarantee, production guarantee, or automatic agent-retirement rule.

The first empirical claim is withheld until exactly 20 genuinely post-freeze, ledgered handoffs reach the endpoint, outcome labeling occurs only after the all-predictions unlock, class floors are met, and the independent verifier reproduces the preregistered statistical result.
