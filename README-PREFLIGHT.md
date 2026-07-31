# OLP 30-pair preflight-only runner

This drop-in contains **no assignment, perturbation execution, scoring, unblinding, or condition-map code**.

It exists only to move the already-frozen preflight into a network-capable, non-scoring GitHub Actions runner.

## Frozen commitments

- benchmark design: `fd0b9eb2e2f494031bac8448dba3f6344071a4d8f2ea9285d2c3fd8ecc159f7f`
- pair set: `5c622e0deaf500f7f39d9c5afece7550c1fa4859155d8b7191eb67bb0a725533`
- scope-repaired signal schema: `88dbb498881e84e32b7599ec2ec1bf186a923bc17d7e0c67c4d09cf2b9cddb8d`
- scope-repaired perturbation spec: `1a94515d691b86719b17885c3bf983fbe7d052affebc22406ec35b34fc9bc9e4`
- preserved scope-mismatch failure: `7383db129c7121a06350eb19ca83e40080d17811a64f362dd2c2c04b1d6aaa9b`
- preserved runner-network failure: `34bdd220ff865421b3d1ba3c014274ae553e037288e1d2ec3619713187d78e68`

## What the job does

It verifies the frozen bytes, performs **30/30 actual Git parent checkouts** from `<task_commit_sha>^1`, checks the exact frozen model/tool/budget declarations, proves the runner can enforce a network-denied subprocess for agent tools, and only after those gates pass makes one **non-benchmark** Responses API capability call using `gpt-5.5-2026-04-23` with medium reasoning.

No benchmark pair/task content is sent to the model during preflight.

A successful job emits `PREFLIGHT_PASS.json` and its SHA-256. A failure emits `PREFLIGHT_BLOCKED.json` and stops. It never randomizes.

## Required GitHub secret

The repository/branch running this workflow must expose `OPENAI_API_KEY` to Actions. Absence or lack of access to the pinned snapshot fails closed.

Use an isolated branch. Pushing these files is sufficient to trigger the preflight workflow; `workflow_dispatch` is also enabled.
