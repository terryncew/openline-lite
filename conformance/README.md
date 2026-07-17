# Conformance checkpoint

Run from the repository root after installation:

```bash
python -m conformance.run
```

The checkpoint covers a native commit, missing-evidence quarantine, the perfectly signed but unsupported hostile control, a mapped foreign receipt, a re-signed false receiver decision, and a two-item verified chain projected down to the latest receiver-approved handoff fact. Test keys are deterministic public fixtures and must never be used outside tests.
