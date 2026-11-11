> Superseded by [AUTOMATIC-REFRESH.md](AUTOMATIC-REFRESH.md). The manual
> `recovered-refresh.yml` candidate workflow below remains as a no-publish
> dry run.

# Recovered refresh: parked synthetic wiring

The recovered-refresh workflow is manual and synthetic only. It calls the actual
canonical reducer, replays an event twice, validates the aggregate and scans the
tracked tree. It reads no private repository, uses no Environment or secret,
installs no key, uploads no artifact and writes no dashboard data. It has no
schedule or publication capability. A successful run is not automatic-update proof.

The real source-fetch/decrypt/adapter/publish stages remain disabled and absent
until the reviewed design and explicit installation decision permit them. Before
adding them, verify a protected Environment admits exactly main (not tags), prove
that a malicious same-repository feature workflow cannot obtain its secrets, and
use a parking-reports-only contents-read credential. Never execute private source
code. The source adapter must enforce immutable snapshots, size/no-follow limits,
hash/schema checks, and strict aggregate/path/privacy gates. No branch or workflow
can authorize its own secrets, auto-merge or settings changes.

Remaining: bounded encrypted-source adapter, current ledger overlay and private
email-intake freshness, candidate renderer, no-op/diff validation, protected data
PR writer, real scheduled folder-event proof and live desktop/mobile verification.
Charts follow the runner priority in issue #45 and use aggregate JSON only.
