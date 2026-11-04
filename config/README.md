# Reviewed source preconditions

The encrypted baseline is pinned to private PR320's exact restore-verified commit,
not assumed to exist on private main. The workflow separately reads current main
ledger/folders. It does not merge the private PR or bypass private CI protections.
The config pins verified union ciphertext and plaintext SHA256. age authenticates
its encryption envelope; exact plaintext hash is checked before JSON is written.
The raw plaintext union is renamed to reports_raw_recovered_union.json for the
adapter, not extracted as an archive. Other encrypted bundle members are not read.
The encrypted inner provenance manifest and outer manifest were restore-verified
by the storage owner; this first runner independently pins union bytes directly,
not a mutable unverified manifest chain. Changing baseline requires reviewed config.

Secrets are Environment parking-source-main only. Main/schedule-or-dispatch guard
applies before private access. The public job never executes private source code.
Ephemeral key and both private checkouts are removed in always cleanup. No artifact
or cache carries private data. A failed decrypt/hash/schema/scan leaves live main
untouched. Locally generated files become one normal data PR, never a main push.

Publication is a normal protected merge, no automatic merge. GITHUB_TOKEN-created
PRs may not automatically trigger CI. A trusted owner must trigger required checks
through an approved existing route. Pages runs on ordinary merged main. This
candidate updates strict aggregate/freshness JSON only; HTML graph refresh remains
a subsequent renderer integration, so no claim that current HTML auto-updates yet.
This PR has manual dispatch only. A timer remains held until a reviewed manual
E2E succeeds; a lower cadence matching the monthly intent needs a later review.


The agreed initial mode uses contents READ
only and uploads exactly recovered-summary.json and source-freshness.json (each
at most512KiB), after strict schema/privacy validation. No push, PR creation,
write token or unattended publication. Existing live main is untouched. Artifact
contains no ciphertext/private paths/hashes/manifests or raw logs. Upload retention
is3days; alwayscleanup removes keys, plaintext, both private checkouts and local
candidate files after upload/failure. Parent creates/reviews data PR separately.

Diagnostic boundary: source_refresh.main sanitizes exceptions from the protected
refresh execution only. Workflow supplies fixed trusted argv; argparse parsing
happens before that protected try and its usage errors are not covered by the
stage/code guarantee. Do not pass source-derived argv. BaseException events such
as KeyboardInterrupt/SystemExit are not caught by the diagnostic handler; the
workflow's always cleanup still owns deletion on any process/job failure. The
guarantee is not that every possible interpreter/runtime message is sanitized.
