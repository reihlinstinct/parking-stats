# Contributing

Small, reviewable changes only: one issue per change, one PR per issue.

- Python 3.11+, standard library only for the core tooling.
- Tests: `python -m unittest discover -s tests` (no pytest).
- Lint: `ruff check src tests` (version pinned in CI).
- Types: `mypy src`.
- The privacy scan (`python -m parking_stats.privacy_scan .`) must pass;
  never commit raw exports or anything with plates, names or case text.
