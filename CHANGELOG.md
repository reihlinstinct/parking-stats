# Changelog

## Unreleased

- Automatic refresh and publish: `pages.yml` now rebuilds the aggregates from
  the private source every 6 hours, on every push to main and on demand, and
  deploys them without committing anything (docs/operations/AUTOMATIC-REFRESH.md).
- Canonical city opening events (`ledger/stats-city-events.json`) join the
  baseline by exact case number; freshness schema is now version 2.
- Ledger rows with `ref_mapping=unverified_fifo` and UUID event numbers no
  longer produce case numbers or abort the refresh.
- The private fetch accepts empty surplus CSV cells (historical rows) and still
  rejects non-empty overflow.
- New renderer `parking_stats.recovered_page` builds the landing page from the
  validated aggregates (catalog strings in Hebrew and a French draft).

- Repository scaffolding: community files, CI (ruff, mypy, unittest,
  gitleaks), OpenSSF Scorecard, privacy scan and a
  GitHub Pages placeholder.
