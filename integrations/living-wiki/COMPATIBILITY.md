# Living-Wiki Compatibility Check

Inspected 2026-09-20. Read-only; no contact, no PRs, no issues, no forks.

## 1. Astro-Han/karpathy-llm-wiki

- Repository: https://github.com/Astro-Han/karpathy-llm-wiki
- Last updated: ~59 days before inspection (per GitHub).
- Folder conventions: `raw/` (immutable source material) → agent compiles →
  `wiki/` (compiled markdown pages, topic subdirectories) + `index.md` +
  `log.md` (append-only operation log).
- Agent layer: Agent Skills standard; installable via
  `npx add-skill Astro-Han/karpathy-llm-wiki`; works with Claude Code,
  Cursor, Codex CLI, OpenCode. Ingest / Query / Lint operations.
- Fit: yes. `openline-wiki init` runs once at the project root; the existing
  ingest skill calls `openline-wiki record` after compiling/updating each
  page; `openline-wiki scan` runs before relying on the wiki. No change to
  their compiler architecture: sidecars live under `.openline/wiki/`, the
  wiki markdown is never modified.
- Design tension, recorded honestly: their Design Boundaries section
  explicitly rejects source-hash freshness tracking ("raw/ is immutable, so
  hashes guard against events that cannot happen"). Their model assumes raw
  never changes. openline-wiki is for wikis where sources CAN change,
  disappear, or be superseded — the failure mode they handle manually
  ("Retract / bad-source machinery — has not happened yet. Handle it
  manually until it does."). Our adapter does not compete with their lint;
  it covers exactly the retract/reconsideration case they currently leave
  manual.

## 2. sqlcode917/llm-wiki

- Repository: https://github.com/sqlcode917/llm-wiki
- Last updated: ~74 days before inspection (per GitHub).
- Folder conventions: three layers — `raw/` (human writes, model reads only)
  / `wiki/` (+ `index.md`, `log.md`) / `SCHEMA.md` (conventions file).
  Model served by Ollama; plain markdown readable in Obsidian.
- Existing deterministic surface: model-free commands (`curator-status`,
  `maintenance`, `graph`) plus report-only model-assisted audits
  (`semantic-lint` reports stale_claim / possible_supersession / data_gap
  without rewriting pages).
- Fit: yes. Their semantic lint is bounded model judgment about staleness;
  openline-wiki is a deterministic byte-standing layer underneath it:
  "this page's recorded sources changed" is a machine-checkable fact that
  can feed their candidate/maintenance signals. Adding it requires no change
  to their ingest compiler — a record call after page writes and a scan
  alongside their deterministic status commands.

## Conclusion

Both active implementations use the `raw/` + `wiki/` source/compiled
separation with plain markdown, both tolerate an extra agent-side skill, and
neither requires changing its compiler to adopt openline-wiki as a
record-after-compile / scan-before-rely layer. The integration shape is
real. One known design tension (immutable-raw assumption in implementation
1) is recorded rather than argued away.
