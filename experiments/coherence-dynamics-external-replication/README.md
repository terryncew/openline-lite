# Coherence Dynamics External Replication 001

This is the one bounded follow-up justified by the Nebius audit. It recreates the exact frozen 75% Nebius development profile, verifies that its original held-out metrics reproduce, then applies the unchanged simple and simple-plus-CD models to 10,000 non-Nebius trajectories from two independent Thoughtworks source cohorts.

It does not search for a new horizon, refit on external outcomes, tune a threshold, add features, or use model APIs. The Nebius-derived Thoughtworks cohort is excluded before feature extraction.

Primary result: external PR-AUC improvement from adding the frozen CD features to the frozen simple model. A positive result requires >0.02 lift, a task-bootstrap lower bound above zero, no material ROC degradation, and positive lift in both source cohorts.

Run through `.github/workflows/cd-external-replication.yml`. Expected API credit spend: $0.

## Runtime reproduction lock

Replication attempt `30909808362` stopped before external acquisition because the source Brier score differed by `1.104e-8` under newer numerical libraries. This package pins Python 3.11.15 and the complete original audit package environment, verifies it before network access, and preserves the original `1e-12` source gate unchanged.
