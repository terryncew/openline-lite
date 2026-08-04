# Nebius public trajectory audit handoff

This root-ready drop-in adds one isolated experiment and one GitHub Actions workflow to `terryncew/openline-lite`.

The implementation performs no model calls and spends no OpenAI API credit. The only networked activity is downloading the pinned public Nebius trajectory dataset. Scientific status remains `NOT_RUN` until the workflow emits and verifies its runtime receipt.

Target branch: `experiment/nebius-public-trajectory-audit`
Suggested commit: `Run zero-cost Nebius trajectory calibration audit`

The ChatGPT GitHub integration was able to read the repository but returned HTTP 403 for both branch and file creation. No repository mutation or workflow dispatch occurred from this environment.
