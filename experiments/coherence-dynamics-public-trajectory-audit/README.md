# Coherence Dynamics Public Coding-Trajectory Calibration Audit

This experiment performs the zero-model-call public-data test that should precede any further paid mechanism experiment. It asks whether structural features in saved coding-agent trajectories predict independently evaluated task success, and whether Coherence Dynamics CSD candidates add held-out information beyond ordinary churn.

## Execution status

The source package is frozen at `READY_FOR_NETWORKED_EXECUTION`. The GitHub Actions workflow downloads the pinned Nebius data, extracts label-blind features, runs the held-out audit, verifies every binding, and uploads the result without using an OpenAI key or model API.

Model/API budget: **$0.00**  
Real scientific assignments: **0**  
Public saved trajectories: **80,036 expected at the pinned revision**

## Scientific boundary

The outcome is benchmark issue resolution, not later human correction after an agent handoff. A positive result establishes only a coding-trajectory profile on this data and split. It does not validate universal Coherence Dynamics.

The complete frozen Experiment 003 mapper is not reconstructed. Public trajectories do not expose verified dependency edges or structured state fields, so the audit reuses only the exact integer curvature kernel on five declared observable scalar channels. This is kernel reuse, not mapper equivalence.

Coherence Dynamics v1.2 governs metric roles: κ is diagnostic curvature; rolling variance and lag-1 autocorrelation of κ are predictive candidates; VKD remains unavailable without external calibration. Synchrony S is unavailable because no frozen phase mapping exists for coding actions.

The 25%, 50%, 75%, and 100% cut points are **retrospective normalized horizons**: locating them requires the final trajectory length. They test transport and feature value, not a deployable real-time alarm horizon.

## Zero-touch GitHub execution

The workflow `.github/workflows/nebius-audit.yml` runs automatically on its pull request and can also be dispatched manually. It:

1. verifies source integrity and runs all offline hostile tests before network access;
2. resolves and downloads exactly revision `a8a64e5` of `nebius/SWE-agent-trajectories`;
3. loads only `instance_id`, `model_name`, `target`, and `trajectory` from the twelve Parquet shards;
4. streams label-blind feature extraction without loading patches or evaluation logs;
5. runs the frozen development and repository-holdout analysis with grouped bootstrap intervals;
6. emits an immutable result, execution receipt, human-readable summary, source/data hashes, and environment record;
7. uploads only bounded audit artifacts, never the raw public dataset.

## Local offline tests

```bash
python -m pip install -r requirements.txt
PYTHONPATH=src python scripts/run_selftest.py
python scripts/verify_package.py
```

## Networked execution commands

```bash
python scripts/acquire_nebius.py --output data/nebius --revision a8a64e5

mapfile -t INPUTS < <(find data/nebius/data -name 'train-*.parquet' -print | sort)
ARGS=()
for f in "${INPUTS[@]}"; do ARGS+=(--input "$f"); done

PYTHONPATH=src python scripts/run_audit.py prepare \
  "${ARGS[@]}" \
  --data-manifest data/nebius/DATA_MANIFEST.json \
  --output artifacts/prepared

PYTHONPATH=src python scripts/run_audit.py run \
  --prepared artifacts/prepared \
  --data-manifest data/nebius/DATA_MANIFEST.json \
  --output artifacts/result \
  --bootstrap-iterations 500

python scripts/verify_package.py \
  --result-dir artifacts/result \
  --prepared-dir artifacts/prepared \
  --data-manifest data/nebius/DATA_MANIFEST.json
```

## Frozen comparisons

- trajectory length only;
- simple structural churn;
- CD curvature/CSD candidates only;
- simple churn plus CD.

The decisive question is incremental held-out value over simple churn. A sophisticated metric does not win merely by correlating with success or failure.
