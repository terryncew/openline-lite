# Security policy

OpenLine Lite is an alpha reference implementation. Do not use its raw-file private-key format as a production key-management system.

Do not deploy v0.3.0. A deeply nested unauthenticated JSON document could escape ingestion as `RecursionError` and terminate the caller. v0.3.1 replaces recursive validation and fails closed at fixed depth and node limits.

Report suspected vulnerabilities privately to the repository owner before opening a public issue. Include the affected version, minimal reproduction, expected security property, and observed result. Avoid including real credentials or customer evidence.

Security-sensitive invariants include:

- untrusted embedded keys remain undecidable;
- invalid signatures fail closed;
- unsupported formats remain undecidable;
- missing claim rules never pass;
- evidence hash or claim mismatches fail;
- decision dispositions are recomputed during verification;
- evidence manifest paths cannot escape their base directory.
- a native chain must preserve parent linkage, sequence, issuer, and run ID;
- only pinned gate decisions under an exact allowed policy hash can authorize handoff facts;
- an eligible non-commit decision conflicts with and excludes a commit;
- source-authored claims stay out of the compact prompt projection;
- manifests, chains, decisions, facts, claims, and evidence bytes have explicit limits.
- canonical JSON depth and node count have fixed limits enforced without recursive validation;
- JSON Pointer array indexes are ASCII-only and bounded before conversion.

Production adopters should replace raw key files with a platform key service, set resource limits appropriate to their environment, pin dependency versions, and obtain an independent security review.

The compact prompt header uses shortened hash markers. Do not use those prefixes as a cryptographic verification interface. Verify full hashes and signatures against the retained objects before constructing the prompt.
