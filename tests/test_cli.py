from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

from openline_lite.canonical import sha256_hex
from openline_lite.cli import main
from openline_lite.crypto import public_key_hex


class CLITests(unittest.TestCase):
    def test_issue_decide_and_verify_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            producer_key = root / "producer.key"
            gate_key = root / "gate.key"
            source_payload = root / "source-payload.json"
            source_receipt = root / "source-receipt.json"
            decision_receipt = root / "decision-receipt.json"
            artifact = root / "evidence" / "tool-output.json"
            evidence_manifest = root / "evidence-manifest.json"
            chain_manifest = root / "chain-manifest.json"
            decisions_manifest = root / "decisions-manifest.json"
            trust = root / "trust.json"
            gate_trust = root / "gate-trust.json"
            policy = root / "policy.json"
            artifact.parent.mkdir()
            artifact.write_bytes(b'{"found":true}')

            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["keygen", "--out", str(producer_key)]), 0)
                self.assertEqual(main(["keygen", "--out", str(gate_key)]), 0)

            issued_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            source_payload.write_text(
                json.dumps(
                    {
                        "schema": "olp.source.v1",
                        "issuer": "cli-agent",
                        "issued_at": issued_at,
                        "run_id": "cli-run",
                        "sequence": 0,
                        "action": {"type": "tool_call", "name": "lookup"},
                        "claim": "Record found.",
                        "evidence": [
                            {
                                "id": "tool-output",
                                "sha256": sha256_hex(artifact.read_bytes()),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            producer_public = public_key_hex(
                producer_key.read_text(encoding="ascii").strip()
            )
            gate_public = public_key_hex(gate_key.read_text(encoding="ascii").strip())
            trust.write_text(
                json.dumps({"cli-agent-key": producer_public}), encoding="utf-8"
            )
            evidence_manifest.write_text(
                json.dumps({"tool-output": "evidence/tool-output.json"}),
                encoding="utf-8",
            )
            policy.write_text(
                json.dumps(
                    {
                        "policy_id": "cli-policy",
                        "version": "1",
                        "allowed_actions": ["tool_call"],
                        "required_evidence": ["tool-output"],
                        "claim_rules": [
                            {
                                "id": "found",
                                "evidence_id": "tool-output",
                                "pointer": "/found",
                                "expected": True,
                            }
                        ],
                        "max_age_seconds": 300,
                        "on_undecidable": "QUARANTINE",
                        "rollback_supported": False,
                    }
                ),
                encoding="utf-8",
            )
            chain_manifest.write_text(
                json.dumps([source_receipt.name]), encoding="utf-8"
            )
            decisions_manifest.write_text(
                json.dumps([decision_receipt.name]), encoding="utf-8"
            )
            gate_trust.write_text(
                json.dumps({"cli-gate": gate_public}), encoding="utf-8"
            )

            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(
                        [
                            "issue",
                            "--payload",
                            str(source_payload),
                            "--key",
                            str(producer_key),
                            "--key-id",
                            "cli-agent-key",
                            "--out",
                            str(source_receipt),
                        ]
                    ),
                    0,
                )
                self.assertEqual(
                    main(
                        [
                            "verify-chain",
                            "--manifest",
                            str(chain_manifest),
                            "--trust",
                            str(trust),
                        ]
                    ),
                    0,
                )
                self.assertEqual(
                    main(
                        [
                            "decide",
                            "--source",
                            str(source_receipt),
                            "--trust",
                            str(trust),
                            "--policy",
                            str(policy),
                            "--evidence",
                            str(evidence_manifest),
                            "--gate-key",
                            str(gate_key),
                            "--gate-id",
                            "cli-gate",
                            "--now",
                            issued_at,
                            "--out",
                            str(decision_receipt),
                        ]
                    ),
                    0,
                )
                self.assertEqual(
                    main(
                        [
                            "verify-decision",
                            "--receipt",
                            str(decision_receipt),
                            "--gate-id",
                            "cli-gate",
                            "--gate-public-key",
                            gate_public,
                        ]
                    ),
                    0,
                )
                self.assertEqual(
                    main(
                        [
                            "handoff",
                            "--chain",
                            str(chain_manifest),
                            "--decisions",
                            str(decisions_manifest),
                            "--producer-trust",
                            str(trust),
                            "--gate-trust",
                            str(gate_trust),
                            "--policy",
                            str(policy),
                            "--format",
                            "jsonl",
                        ]
                    ),
                    0,
                )

    def test_demo_command_includes_hostile_control(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["demo"]), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["complete"]["decision"], "COMMIT")
        self.assertEqual(result["signed_but_unsupported"]["decision"], "DENY")


if __name__ == "__main__":
    unittest.main()
