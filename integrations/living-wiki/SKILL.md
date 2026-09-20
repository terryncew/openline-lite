# Living-Wiki Standing (openline-wiki)

A tiny standing layer for LLM-maintained wikis with a source/compiled
separation (e.g. `raw/` + `wiki/`). It records which source versions each
compiled wiki page was declared to depend on, and later flags pages for
reconsideration when those recorded sources change or disappear.

It does NOT determine whether a wiki claim is true. It determines whether a
compiled page still has the source standing it was recorded against.

No server, no database, no model call. Plain markdown files are enough.

## Setup (once per wiki project)

```sh
cd my-wiki
openline-wiki init --source-dir raw --wiki-dir wiki
```

This creates `.openline/wiki/` sidecars. Your markdown is never modified.

## WHEN INGESTING (compiling or updating wiki pages)

1. Compile/update wiki pages normally.
2. For each modified wiki page, identify the raw sources actually used.
3. Record the dependency set:

```sh
openline-wiki record wiki/topic.md \
  --source raw/paper-a.md \
  --source raw/notes-b.md \
  --complete
```

4. Use `--complete` only when the workflow explicitly treats the supplied
   dependency set as complete. Otherwise omit the flag (defaults to
   incomplete).
5. If you do not know whether you captured every material dependency,
   record incomplete. Incomplete is honest; silent completeness is not.

The record establishes: "this compiled page was recorded as depending on
these exact source versions." It does NOT establish: "these were every
source that materially influenced the page." The complete/incomplete bit
exists precisely to preserve that distinction.

A page whose own bytes changed since its record was created is flagged as
UNDETERMINED until a new record is deliberately created for the new state.
Re-recording is deliberate: run `openline-wiki record` again after
recompiling the page against the new source state.

## BEFORE RELYING ON THE WIKI FOR A NEW TASK

```sh
openline-wiki scan
```

- REOPEN: a recorded source changed or disappeared. Surface or recompile
  the page before relying on it as settled support.
- UNDETERMINED: standing cannot be established (no record, incomplete
  capture, stale record, malformed sidecar). Treat as unresolved, not
  current. Missingness never silently becomes independence.
- RETAIN: all recorded sources are byte-identical to the record AND the
  dependency declaration was explicitly marked complete. This means source
  standing is unchanged, not that the content is true.

`openline-wiki context` emits the same partition as deterministic JSON for
pasting into agent context. No retrieval, no ranking, no summarization.

## Limits (say these plainly)

- Truth detection: no. Automatic correction: no. Provenance discovery: no.
- Hallucination prevention: no. Independent dependency verification: no.
- Cryptographic proof of truth: no.
- Protection against a malicious agent with unrestricted filesystem access: no.

Maximum claim: openline-wiki records which source versions a compiled wiki
page was declared to depend on and deterministically flags that page for
reconsideration when those recorded sources change or disappear.

Short version: your wiki remembers when its sources stopped matching.
