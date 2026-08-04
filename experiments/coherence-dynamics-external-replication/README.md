# Coherence Dynamics External Replication 003

Replication 002 stopped before external scoring because the pinned Thoughtworks corpus is not outcome-complete across both planned non-Nebius cohorts. The first visible exception came from a null `recorded_model` in a Klear row, but `recorded_model` is provenance metadata rather than trajectory identity. More importantly, the Klear rows in the pinned file do not contain an independently evaluated `resolved` outcome, so they cannot support predictive validation.

Replication 003 is a preregistered schema repair, not a result rescue. It preserves the recovered Nebius source profile, 75% horizon, feature order, selected C values, source thresholds, bootstrap rule, and external numeric pass/fail gate. It scores only the 5,000-row label-complete `swe-smith-claude-3-7-sonnet` cohort. The 5,000 Nebius-derived rows remain excluded for source overlap, and the 5,000 Klear rows are explicitly recorded as `EXCLUDED_NO_OUTCOME_LABEL`; no labels are invented or inferred.

The workflow writes an external schema audit before scoring and fails closed if source counts or included-label completeness differ from the frozen protocol. No external fitting or threshold tuning is permitted.

API/model calls: 0. API credit spend: $0.
