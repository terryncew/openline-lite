# OLP Core 2.1 30-pair execution-only runner

Experiment: `olp-core21-paired-mechanism-001`

This drop-in is for a **fresh branch from the exact green preflight commit**:

- source branch: `preflight/olp-core21-paired-mechanism-001`
- green source commit: `54d906cce8354bd58d1fd664a5028c4e0ec1f0be`
- intended execution branch: `run/olp-core21-paired-mechanism-001`

It is an execution harness, not a scientific redesign. The frozen benchmark files and successful preflight receipt are copied byte-for-byte into `experiments/paired-mechanism-execution/frozen/` and verified before any assignment.

## Frozen evidence

- `BENCHMARK_DESIGN_FROZEN.json` — `fd0b9eb2e2f494031bac8448dba3f6344071a4d8f2ea9285d2c3fd8ecc159f7f`
- `PAIR_SET_FROZEN.json` — `5c622e0deaf500f7f39d9c5afece7550c1fa4859155d8b7191eb67bb0a725533`
- `SIGNAL_SCHEMA_FROZEN_SCOPE_REPAIRED.json` — `88dbb498881e84e32b7599ec2ec1bf186a923bc17d7e0c67c4d09cf2b9cddb8d`
- `PERTURBATION_SPEC_FROZEN_SCOPE_REPAIRED.json` — `1a94515d691b86719b17885c3bf983fbe7d052affebc22406ec35b34fc9bc9e4`
- `PREFLIGHT_BLOCKED_SCOPE_MISMATCH.json` — `7383db129c7121a06350eb19ca83e40080d17811a64f362dd2c2c04b1d6aaa9b`
- `PREFLIGHT_BLOCKED_RUNNER_NETWORK.json` — `34bdd220ff865421b3d1ba3c014274ae553e037288e1d2ec3619713187d78e68`
- `RESEALED_AFTER_SCOPE_REPAIR.json` — `050e8e643d04af4c0c18158d084110ea2b001a33ea57d8d5d22bd32e95501564`
- `PREFLIGHT_PASS.json` — `3b2aa2ca991b82a343ac7a4bdc953947f4db45f07bf072925da07c02182b6d98`

The successful preflight establishes 30/30 exact Git parent checkouts, access to `gpt-5.5-2026-04-23`, medium reasoning, the frozen local-tool/budget boundary, zero benchmark model calls, and no real assignment before the execution phase.

## Execution architecture

The GitHub Actions workflow is **exact-tag-only**. Pushing the run branch does **not** run the experiment. The original trigger tag `RUN_REAL_OLP_CORE21_PAIRED_MECHANISM_001` is preserved as historical evidence of the first infrastructure-only failure, which stopped before assignment. The repaired runner triggers only from the new exact tag `RUN_REAL_OLP_CORE21_PAIRED_MECHANISM_001_RETRY1` on the reviewed repaired commit. Do not delete, move, or recreate the original tag. A repository-level assignment-lock artifact plus prior `assign-once` job history blocks a second assignment attempt whenever `assign-once` actually ran; a prior run whose `assign-once` job was skipped is not treated as an assignment attempt.

The workflow separates outputs into three artifacts:

**A — public/scorer material**

`olp-30pair-PUBLIC-SCORER-EXECUTION-BUNDLE`

Contains the blinded manifest, 60 opaque traces or frozen invalid-run records, public verification records, completeness receipt and hashes. It contains no assignment labels, plaintext condition map, or key.

**B — sealed condition material**

`olp-30pair-sealed-condition-material`

Contains an AES-256-GCM encrypted condition map, the pre-execution plaintext SHA-256 commitment, and encryption verification metadata. Before hashing, the encrypted plaintext includes a fresh secret 256-bit commitment nonce; that nonce is not present in public/scorer material, preventing brute-force recovery of the 30 binary assignments from the public commitment. It contains no plaintext map and no key.

**C — secret key material**

`olp-30pair-secret-key-material-DO-NOT-SCORE`

Contains only `secret_key.bin`. Keep this outside the scoring path. Do not upload it to the blind scorer before all scores and the blinded aggregate are sealed.

The `execute-pairs` jobs decrypt their pair assignment only in private job memory, remove the downloaded key and sealed bundle from the job filesystem before any model/tool execution, and export only opaque structural traces.

## Agent/tool boundary

The orchestrator itself has network access so it can call the OpenAI Responses API. The model receives only five custom tools from the frozen allowlist:

- `read_file`
- `list_tree`
- `search_text`
- `apply_patch`
- `run_shell`

`run_shell` executes inside a separate network and PID namespace. The tool environment excludes `OPENAI_API_KEY`, `GITHUB_TOKEN`, assignment material, and runner secrets. The model has no web/file-search/MCP/external-memory tool.

The historical child commit/diff is never exposed to the agent workspace. The runner uses the parent SHA already sealed by the green preflight, fetches **only that exact parent SHA** at depth 1, and checks it out detached. It does not fetch the historical child/task commit into the agent workspace object database.

## Operational trace mapper

`TRACE_FORMAT.json` is an execution-format commitment made before assignment. It only realizes the already-frozen required observations: write count, revision count, typed dependency-edge identities, and canonical state-field values. It does not add a contradiction label, severity score, condition feature, graph label, threshold, smoothing change, or κ calculation.

The scorer computes κ later. This runner never computes κ and contains no unblinder.

## Local dry verification

From repository root:

```bash
python -m pip install cryptography==46.0.4
python experiments/paired-mechanism-execution/release_check.py
```

The release check uses disposable temporary assignments only. It does not call OpenAI and does not create the real condition assignment.

## Repository secret

The only user-created GitHub Actions repository secret required is:

`OPENAI_API_KEY`

GitHub provides `GITHUB_TOKEN` automatically to the assignment-lock guard. It is never passed to the agent shell.

## Exact retry-tag real-run trigger

Do not create the retry trigger tag while reviewing the branch. Preserve the original `RUN_REAL_OLP_CORE21_PAIRED_MECHANISM_001` tag unchanged as historical evidence.

When you are deliberately ready to create the irreversible real assignment:

1. Create `run/olp-core21-paired-mechanism-001` from commit `54d906cce8354bd58d1fd664a5028c4e0ec1f0be`.
2. Put this root-ready drop-in at repository root and commit/push it to that branch. **That branch push does not run the experiment.**
3. Confirm `OPENAI_API_KEY` exists under repository **Settings → Secrets and variables → Actions → Repository secrets**.
4. Review the pushed run-branch commit and verify it descends from the green preflight commit.
5. On that exact reviewed commit, create the tag named exactly:

   `RUN_REAL_OLP_CORE21_PAIRED_MECHANISM_001_RETRY1`

6. Push that retry tag exactly once. The retry-tag push is the irreversible scientific trigger.
7. Immediately download and separately preserve the sealed-condition artifact and secret-key artifact when the assignment job succeeds; do not send the key to the scorer.
8. Do not use **Re-run all jobs** or **Re-run failed jobs**. `GITHUB_RUN_ATTEMPT != 1` is rejected, and a delete/recreate-tag attempt is blocked by prior `assign-once` job history or the assignment-lock artifact.

A real tag-triggered run first verifies the exact tag, green-base ancestry, all frozen evidence, and the runner manifest. If that gate fails, it emits `EXECUTION_BLOCKED` with zero benchmark model calls and zero real assignments. Only a passing gate reaches the one-time assignment job.

## Claim boundary

This is a controlled paired mechanism benchmark testing whether frozen κ responds directionally to one frozen upstream information-loss intervention. It is not predictive-validity evidence and does not establish general contradiction detection.

`REAL_ASSIGNMENT_NOT_CREATED`  
`BENCHMARK_MODEL_CALLS_0`  
`UNBLINDED_FALSE`
