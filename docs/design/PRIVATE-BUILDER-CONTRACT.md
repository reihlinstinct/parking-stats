# Private checkout builder contract

Issue #36. Trusted public-runner code only. This module is the offline adapter;
the surrounding workflow owns source authentication, decryption, output staging,
public diff gates, publication and cleanup. No execution occurs in the private
repository. Do not enable until independent review and synthetic CI pass.

## Layout and invocation

Resolve one immutable private parking-reports commit with read-only credentials.
Checkout as an immutable data directory, with no submodule/LFS/hooks execution.
The adapter reads ledger/reports.csv and each nonempty metadata_record only when
it is exactly reports/<folder_id>/report.json. It rejects symlinks, missing files,
non-files, traversal, duplicate folder IDs, duplicate JSON keys, invalid dates,
reference conflicts, nonempty overflow CSV cells and bounded-size violations.
A referenced report JSON is validated as an object, never executed; its nested
contents are not automatically promoted to municipal facts in this first adapter.

The workflow separately decrypts the approved encrypted recovery file into an
ephemeral restricted directory containing reports_raw_recovered_union.json.
Verify ciphertext/source hashes against the approved encrypted provenance manifest
before passing this directory. Neither age identity nor read token is an adapter
argument. Never emit decrypted data to stdout, cache, artifacts or public checkout.
The adapter does not decrypt by subprocess or execute private files. Cleanup is
the workflow's finally/always responsibility. Immutable files during execution are
a precondition; the reader is not a concurrent hostile-filesystem sandbox.

Call parking_stats.private_builder.build(private_root, decrypted_root,
streets=reviewed_public_street_set, categories=reviewed_public_category_set,
ledger_observed_at=timezone_aware_ISO_timestamp). It returns (aggregate,freshness).
All failures raise sanitized UnionError codes, not source contents. Caller logs
only the safe code, never traceback locals or raw input. Neither returned object
contains case IDs, email IDs, private revisions, paths, plates or free text.

## Recovered input

Root is an object with timezone-aware snapshot_at, incidents array, conflicts
array and unresolved_filed_rows array. Each incident has a unique complete
municipal case number (including /suffix), origin from email_export/ledger/
city_email, and nullable original-export fields. Only createdon, streetname,
subjectname and ClosedDateIncident are passed to the reviewed reducer. Other
private evidence stays in the encrypted source. Invalid reducer fields fail.

Conflict entries carry an existing case number, one reducer field, email_value
and a bounded message_id. They become separate private source events so different
source facts are not silently overwritten. Conflicting days/labels become unknown
in public aggregates. No raw status statistic is produced. Duplicate baseline
case numbers fail rather than being silently dropped.

## Ledger updates and limitations

The current ledger enriches an existing full case number or adds one new numbered
case. A GUID is not a municipal case number. municipal_event_number may supply a
number only if it is valid and does not contradict municipal_ref. Date/street
alone never links unresolved filings. Unnumbered/GUID-only rows stay outside the
numbered total. Prepared-only rows are excluded. Prior unresolved folders absent
from the current ledger remain unresolved, never silently erased.

Only city_opened_at becomes createdon. filed_date is validated but is not silently
substituted as municipal opening date. Internal ledger event_type is not an
invented official category; new cases without official category go to unknown.
Report JSON free text, notes and registry fields cannot cross this boundary.
The fixed original recovery remains immutable; reducer facts update from current
ledger rows while conflicting baseline facts remain preserved as uncertainty.

## Email staleness and output

This adapter reads NO inbox and makes NO municipal API calls. Its email source
is the recovered encrypted snapshot. Freshness output states email_stale=true
and email_refresh_mode=historical_snapshot_not_live, with the source snapshot
and ledger observation timestamps separately. It never calls an unchanged email
snapshot fresh because the ledger was checked now. Later email intake requires a
separate reviewed input contract; do not claim automatic email coverage yet.

Aggregate output is the merged reducer's strict exact-key public schema, using
only caller-reviewed labels. Unknown/unapproved labels are bucketed. Freshness
has only schema_version,refresh_mode,ledger_observed_at,email_snapshot_at,
email_refresh_mode,email_stale with fixed mode values. Workflow must reject extra
freshness keys and validate timestamps/modes again before publication. Semantic
no-op comparison ignores a changed observation clock alone; do not create hourly
timestamp-only commits. Publish neither private source revision nor union.

Limits: 20 MB per file, 100 MB total reads, 100,000 ledger/baseline cases, reducer
input cap and strict public count limits. No fallback partial build on errors.
A malformed new record leaves previous public output untouched. Public workflow
must still apply exact output-path/diff/schema/privacy gates and normal protection
rules. This callable does not grant public write access or prove automatic runs.
