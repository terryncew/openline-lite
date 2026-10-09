# AI agent protocol comparison: what survives a provider switch?

*Documented comparison, 8 October 2026. This is a test scenario, not a ranking or a claim that the protocols are interchangeable.*

AI agent protocols are often discussed together even when they solve different problems. **A concrete test:** an owner has authorized Agent A to take a limited action. The owner revokes A and switches to Agent B. What must the receiving service check, and what evidence survives the switch?

This page puts OpenLine in context with [MCP](https://modelcontextprotocol.io/), [A2A](https://a2a-protocol.org/), [Google AP2](https://github.com/google-agentic-commerce/AP2), and [Microsoft's Agent Governance Toolkit](https://github.com/microsoft/agent-governance-toolkit). The sources, executable example, observed result, and limitations are below. **A protocol not documenting this exact scenario is not evidence that it cannot support it.**

## What each project is addressing

| Project | What its own specification or documentation covers | Relevant source |
| --- | --- | --- |
| **MCP (Model Context Protocol)** | Connecting AI applications to tools and data. Its HTTP authorization specification uses OAuth-style access control; authorization support is optional at the protocol implementation level. | [MCP authorization specification, 2026-07-28](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/authorization/index.mdx) |
| **A2A (Agent2Agent)** | Communication and task collaboration between independent agents. Its specification also assigns request authorization to the receiving A2A server under its own policy. | [A2A specification](https://github.com/a2aproject/A2A/blob/main/docs/specification.md) |
| **AP2 (Agent Payments Protocol)** | Cryptographically signed mandates and receipts for agent-mediated checkout and payment. It already provides substantive proof of payment authorization and merchant/payment results; signed receipts are **not** unique to OpenLine. | [AP2 specification](https://github.com/google-agentic-commerce/AP2/blob/main/docs/ap2/specification.md) |
| **Microsoft Agent Governance Toolkit** | Policy enforcement, identity, compliance and audit components for agent systems. Independent signed compliance receipts are also addressed in a **draft proposal**, which should not be described as a universally deployed feature. | [Toolkit](https://github.com/microsoft/agent-governance-toolkit) and [draft receipt proposal](https://microsoft.github.io/agent-governance-toolkit/proposals/verifiable-compliance-receipts/) |
| **OpenLine Wallet and Receiver Gate** | Owner-held signed permission and revocation history, with the receiving system checking current authority for the exact action and signing its disposition. It has a bounded provider-switch experiment. | [Wallet README](https://github.com/terryncew/openline-wallet) and [PLATFORM-EXIT-LIVE-001](https://github.com/terryncew/openline-wallet/blob/main/PLATFORM_EXIT_LIVE_001.md) |

These are **overlapping layers, not five substitutes**. An OpenLine real-host trial used MCP for the tool-call transport and the OpenLine Receiver Gate for the authority decision. It does not follow that MCP has no authorization, or that AP2 has no receipts.

## The provider-switch test

Imagine a deploy action that is allowed only in a safe staging environment. The same owner controls permission history; the receiving service decides whether each proposed action is allowed. The owner can replace the agent vendor without changing the receiver's rules.

An implementation trying this scenario should publish evidence for the following five checks:

1. **Before the switch:** Agent A receives permission for the exact staging action. The receiver allows one attempt and records the effect and signed decision.
2. **Change the authority:** The owner revokes A and authorizes B for the same limited action. A's previously issued signature remains cryptographically genuine, but its permission is no longer current.
3. **Try the old worker again:** A attempts the action after revocation. The receiver rejects it. **No additional effect** occurs.
4. **Use the replacement:** B attempts the action under the current owner permission. The receiver accepts it and records its effect.
5. **Inspect the history:** A third-party verifier can inspect the signed authorization history and receiver decisions without obtaining A's provider credential or relying on A's account of what happened.

The minimum comparison evidence is: exact protocol/spec version; who holds the signing keys; which system enforces the decision; event/effect sequence; what can be verified independently; reproducible code or test outputs; and the **unproven** cases. A system may address only part of this scenario. Mark that **not evaluated** or **outside stated scope**, not **failed**.

## What OpenLine has actually demonstrated

[PLATFORM-EXIT-LIVE-001](https://github.com/terryncew/openline-wallet/blob/main/PLATFORM_EXIT_LIVE_001.md) documents a real-host test from 4 September 2026. Claude Code and Codex CLI used separate MCP sidecars against one continuously running local Receiver Gate:

```text
Claude before switch:  ALLOWED
Claude after revocation: STOPPED / MANDATE_REVOKED
Codex after switch:    ALLOWED
Recorded effects:     2
Signed receipts:      3
```

The [frozen evidence](https://github.com/terryncew/openline-wallet/tree/main/proofs/platform-exit-live-001) includes results, receipts, effect ledger and host logs. The source report identifies the original [successful CI run](https://github.com/terryncew/openline-wallet/actions/runs/33841050124).

To try the **separate deterministic local demonstration** (not a replay of live model calls):

```bash
git clone https://github.com/terryncew/openline-wallet.git
cd openline-wallet
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
openline-wallet demo-platform-exit
```

Expected final verdict: `PLATFORM_EXIT_CONTINUITY_ENFORCED`. Read the current [README](https://github.com/terryncew/openline-wallet#try-the-provider-switch-demo) for platform-specific instructions.

**Limits:** This is a reference implementation and an intentionally safe local experiment, not a production claim. It does not establish universal provider portability, transfer of provider chat or credentials, hardened key custody, independently operated outside receivers, production restart recovery, or reliable cross-machine revocation timing. A same-user malicious agent can potentially access local files that OS user can read. Signed records prove the integrity of statements, **not** that every action was recorded or that the signer was honest.

## Invite a comparison, not an endorsement

If you maintain another agent protocol or agent-governance implementation, the useful contribution is an **equivalent source-backed result**: can your documented deployment handle this exact owner-revocation/provider-handoff sequence, and where is the authority history kept? Link the implementation or spec, note any missing pieces, and state the constraints.

If the test is outside your project's job, say so. If a comparable implementation predates OpenLine, link it. If a signed receipt or authorization feature overlaps OpenLine, count that overlap rather than hiding it. The goal is an accurate protocol landscape and a test other developers can reproduce—not a fabricated leaderboard.

OpenLine is one independent experimental entry in that landscape. **Independent receiving systems accepting its foreign authority is still an open interoperability test**, not something this page claims to have achieved.
