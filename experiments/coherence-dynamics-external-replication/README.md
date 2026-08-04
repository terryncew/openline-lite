# Coherence Dynamics External Replication 002

This package repairs a reproducibility defect discovered before external execution. The original Nebius audit did not serialize its trained model artifact. Two attempts to retrain that model and match one aggregate Brier value within `1e-12` aborted before acquiring external data, including after exact package-version pinning.

The repaired workflow preserves the source dataset hashes, 75% horizon, repository split, feature order, selected C values, original source thresholds, external cohorts, and external result rule. It fits the final source models once, serializes the imputer, scaler, coefficients, and intercept, hashes and verifies that recovered profile, and only then downloads the independent Thoughtworks data.

The profile is explicitly labeled recovered rather than an exact reconstruction of an artifact the source run did not save. No external fitting or threshold tuning is permitted.

API/model calls: 0. API credit spend: $0.
