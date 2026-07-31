# Calibration Trial data format

## Pre-handoff session: observations only

A prospective session is strict JSON and cannot contain a metric profile, Δhol weights, post-handoff transcript, or outcome.

```json
{
  "schema": "openline.calibration-trial.session.v2",
  "session_id": "session-031",
  "handoff_at_utc": "2026-08-01T04:00:00Z",
  "transcript": [
    {"index": 0, "role": "user", "text": "...", "tool_name": null},
    {"index": 1, "role": "assistant", "text": "...", "tool_name": null},
    {"index": 2, "role": "tool", "text": "...", "tool_name": "python"}
  ],
  "measurement_input": {
    "algorithm_id": "cole-portable-core-2.1-draft",
    "signal_points_micros": [500000, 510000, 520000],
    "previous_graph": {"claims": [], "evidence": [], "relations": []},
    "current_graph": {"claims": [], "evidence": [], "relations": []}
  }
}
```

The receiver-owned computation profile lives only in `protocol.yaml` under `measurement_contract.receiver_profile`.

## Post-handoff continuation

The scorer does not read continuation files. They are retained for later outcome review.

```json
{
  "schema": "openline.calibration-trial.continuation.v1",
  "session_id": "session-031",
  "started_at_utc": "2026-08-01T04:01:00Z",
  "events": [
    {"index": 0, "role": "assistant", "text": "...", "tool_name": null},
    {"index": 1, "role": "user", "text": "Correction: the API contract was frozen.", "tool_name": null}
  ],
  "ended": false
}
```

## Calibration outcome JSONL

Historical calibration labels use `phase: "calibration"` and cannot bind a prospective outcome unlock.

```json
{"schema":"openline.calibration-trial.outcome.v2","phase":"calibration","session_id":"session-010","outcome":1,"kind":"constraint","correction_message_index":1,"continuation_sha256":"<64 lowercase hex>","window_observed_assistant_turns":1,"continuation_ended":false,"labeled_at_utc":"2026-07-01T04:17:00Z","outcome_unlock_sha256":null,"notes":"Dropped frozen API constraint."}
```

## Prospective eligibility JSONL

Every in-trial post-freeze handoff is registered before scoring. The ledger is append-only and capped at `test_n=20`.

```json
{"schema":"openline.calibration-trial.eligibility.v2","trial_id":"olp-handoff-calibration-003","eligibility_index":1,"session_id":"session-031","handoff_at_utc":"2026-08-01T04:00:00Z","registered_at_utc":"2026-08-01T04:00:30Z","protocol_sha256":"<64 lowercase hex>","freeze_sha256":"<64 lowercase hex>","session_sha256":"<64 lowercase hex>","previous_eligibility_hash":null,"eligibility_hash":"<64 lowercase hex>"}
```

Rows must be strictly increasing by `(handoff_at_utc, session_id)`. Registration later than 600 seconds after handoff fails closed.

## Prediction JSONL

Predictions are written in exact eligibility order and hash-chain to the prior prediction. Each row binds the receiver-owned profile hash, exact session bytes, eligibility row, frozen thresholds, computed metrics, baseline predictions, and timestamp.

The prediction must be sealed after eligibility registration, no later than 600 seconds after handoff, and strictly before continuation start.

## Outcome unlock

No prospective outcome may be written through the official label path until an unlock receipt proves all 20 predictions are sealed.

```json
{
  "schema": "openline.calibration-trial.outcome-unlock.v1",
  "trial_id": "olp-handoff-calibration-003",
  "generated_at_utc": "2026-09-01T00:00:00Z",
  "protocol_sha256": "<64 lowercase hex>",
  "freeze_sha256": "<64 lowercase hex>",
  "eligibility_ledger_sha256": "<64 lowercase hex>",
  "eligibility_chain_tail": "<64 lowercase hex>",
  "predictions_sha256": "<64 lowercase hex>",
  "prediction_chain_tail": "<64 lowercase hex>",
  "test_n": 20,
  "claim": "Outcome labeling unlocked only after every preregistered prospective prediction was sealed."
}
```

The unlock file is write-once through the CLI.

## Prospective outcome JSONL

Prospective labels use `phase: "prospective"` and hash-bind the outcome-unlock receipt plus the reviewed continuation.

```json
{"schema":"openline.calibration-trial.outcome.v2","phase":"prospective","session_id":"session-031","outcome":1,"kind":"constraint","correction_message_index":1,"continuation_sha256":"<64 lowercase hex>","window_observed_assistant_turns":1,"continuation_ended":false,"labeled_at_utc":"2026-09-01T00:05:00Z","outcome_unlock_sha256":"<64 lowercase hex>","notes":"Dropped frozen API constraint."}
```

For `outcome: 0`, `kind` and `correction_message_index` are null. The continuation must contain all eight assistant messages or be explicitly marked ended.

## Evaluation

The evaluation binds the protocol, freeze, eligibility chain, prediction chain, outcome unlock, and ordered outcome set. For every baseline it reports:

- balanced-accuracy confusion statistics;
- exact paired-randomization p-value as numerator/denominator and conservative micro-unit rendering;
- 95% exactly enumerated stratified bootstrap percentile interval for the paired balanced-accuracy difference.
