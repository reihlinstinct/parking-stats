# Contributing

Small, reviewable changes only: one issue per change, one PR per issue.

- Python 3.11+, standard library only for the core tooling.
- Tests: `python -m unittest discover -s tests` (no pytest).
- Lint: `ruff check src tests` (version pinned in CI).
- Types: `mypy src`.
- The privacy scan (`python -m parking_stats.privacy_scan .`) must pass;
  never commit raw exports or anything with plates, names or case text.

## File placement (enforced)

Decide where a file lives BEFORE creating it. The contract in `.repo-layout.yml` lists the allowed top-level directories, the file types permitted in each, and the required community-standards files; `python tools/check_repo_layout.py` enforces it in CI. A new kind of file means updating the contract in the same PR.
