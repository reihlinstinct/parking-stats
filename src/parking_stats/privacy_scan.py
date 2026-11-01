"""Repository privacy scan: block private data from the public aggregates.

The aggregate-only rule (see README): this repository publishes counts,
never raw data. This scanner fails CI when a tracked text file contains
patterns that indicate private data:

- Israeli license plates: dashed/spaced forms (XX-XXX-XX, XXX-XX-XXX)
  with any of ``- . space / _`` or Unicode dashes (U+2010-U+2015,
  U+2212) as separators, repeats allowed; and compact forms of 7-8
  digits, each optionally separated by one such separator (covers a
  stray space inside the number). ISO-style dates (YYYY-MM-DD and
  friends) are excluded from the compact rule;
- Israeli mobile numbers: 05X, +972, 972 or 00972 prefixes, an
  optional ``(0)`` after an international prefix, and any mix of
  dashes, spaces, dots, slashes or parentheses between digit groups;
- JSON keys that exist only in raw municipal exports (``description``,
  ``caseId``, ``case_number``, ``ClosedDateIncident``), matched
  case-insensitively, quoted or unquoted, followed by ``:`` or ``=``,
  and the same names as CSV/TSV header columns (quotes and surrounding
  whitespace tolerated).

Every line is normalised before matching: NFKC (full-width digits and
compatibility characters fold to ASCII), zero-width characters
(U+200B-U+200F, U+2060, BOM) are removed, and no-break spaces become
plain spaces.

Paths in ``.privacy-scan-allow`` (one per line, ``#`` comments) are
skipped. An entry is ``path`` to skip the whole file, or ``path rule``
to skip only that rule for the file (``plate``, ``plate-compact``,
``phone``, ``phone-bare``, ``raw-export-key`` or ``raw-export-column``);
prefer the
rule-scoped form so other rules still guard the file. Use it for files
that carry synthetic detection samples and for known-safe collisions
such as package metadata fields named ``description``.

The directive ``section:plates`` waives only the ``plate`` and
``plate-compact`` rules, and only on lines strictly between one
``<!-- repeat-plates:begin -->`` / ``<!-- repeat-plates:end -->``
marker pair in that file. Anything else - no pair, extra markers, a
reversed pair - waives nothing, so a broken section fails closed; the
marker lines themselves are always scanned strictly. It exists for the
generated landing page's repeat-plates section, the single approved
place where plates appear.

Known limitations, accepted by design: matching is per line, so a plate
or key split across two lines is not detected; and the compact plate
rule also matches bare 7-8 digit numbers (aggregate outputs never
contain any, so a hit here is worth a human look either way).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# Separator characters accepted inside plates and phones: ASCII dash,
# dot, whitespace, slash, underscore, and Unicode dashes (raw string so
# the \u escapes reach the regex engine verbatim).
_SEP = r"-.\s/_\u2010-\u2015\u2212"
# Phones additionally tolerate parentheses: "+972 (0)54-..." forms.
_PHONE_SEP = r"-.\s()/\u2010-\u2015\u2212"

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‎‏⁠﻿"), None)


def normalize_line(line: str) -> str:
    """Fold compatibility characters, strip zero-width chars and nbsp."""
    line = unicodedata.normalize("NFKC", line)
    return line.translate(_ZERO_WIDTH).replace(" ", " ").replace(" ", " ")


RULES: dict[str, re.Pattern[str]] = {
    # Dashed/spaced Israeli plates, e.g. XX-XXX-XX, XXX-XX-XXX, with any
    # separator from _SEP, repeats allowed.
    "plate": re.compile(
        rf"\b\d{{2}}[{_SEP}]+\d{{3}}[{_SEP}]+\d{{2}}\b"
        rf"|\b\d{{3}}[{_SEP}]+\d{{2}}[{_SEP}]+\d{{3}}\b"
    ),
    # Compact Israeli plates: 7-8 digits with optional separators between
    # digits, repeats allowed (covers bare runs and stray-separator forms).
    # forms). ISO-style dates (2026-11-01) are excluded by the lookahead.
    # Bare 7-8 digit counts are accepted false positives (aggregates never
    # hold numbers that size).
    "plate-compact": re.compile(
        rf"(?<!\d)(?!\d{{4}}[{_SEP}]\d{{2}}[{_SEP}]\d{{2}}(?!\d))"
        rf"(?:\d[{_SEP}]*){{6,7}}\d(?!\d)"
    ),
    # Israeli mobiles: 05X, +972, 972 or 00972 prefixes, optional (0)
    # after the prefix, any dash/space/dot/slash/paren grouping; the bare
    # 9-digit prefixless form is the phone-bare rule below. Bare 9-digit
    # counts are accepted false positives (aggregates hold none).
    "phone": re.compile(
        rf"\b(?:\+?972|00972|0)[{_PHONE_SEP}]*(?:\(0\)[{_PHONE_SEP}]*)?"
        rf"5\d[{_PHONE_SEP}]*\d{{3}}[{_PHONE_SEP}]*\d{{4}}\b"
    ),
    # Bare 9-digit mobile without a prefix: 5X XXX XXXX, any grouping.
    "phone-bare": re.compile(rf"(?<!\d)5\d(?:[{_PHONE_SEP}]*\d){{7}}(?!\d)"),
    # Raw municipal export keys: case-insensitive, quoted or not,
    # whitespace tolerated, followed by ":" or "=".
    "raw-export-key": re.compile(
        r"[\"']?\s*\b(?:description|caseid|case_number|closeddateincident)\b"
        r"\s*[\"']?\s*[:=]",
        re.IGNORECASE,
    ),
    # The same keys as CSV/TSV header columns, quotes/whitespace tolerated.
    "raw-export-column": re.compile(
        r"(^|[,;\t])\s*[\"']?(?:description|caseid|case_number|closeddateincident)"
        r"[\"']?\s*(?=,|;|\t|$)",
        re.IGNORECASE | re.MULTILINE,
    ),
}

ALLOW_FILE = ".privacy-scan-allow"
TEXT_CHUNK = 1 << 20

# Marker pair delimiting the repeat-plates section of the generated
# landing page; the section:plates directive waives plate rules there.
PLATES_SECTION_BEGIN = "<!-- repeat-plates:begin -->"
PLATES_SECTION_END = "<!-- repeat-plates:end -->"
PLATES_SECTION_RULES = frozenset({"plate", "plate-compact"})
SECTION_DIRECTIVE = "section:plates"


@dataclass(frozen=True)
class Finding:
    """One rule hit; the matched text itself is never stored or printed."""

    path: str
    line: int
    rule: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}"


def load_allowlist(root: Path) -> dict[str, set[str]]:
    """Map path -> rules to skip there; an empty set skips the whole file."""
    allow = root / ALLOW_FILE
    if not allow.exists():
        return {}
    entries: dict[str, set[str]] = {}
    for line in allow.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        path, rules = parts[0], set(parts[1:])
        unknown = rules - set(RULES) - {SECTION_DIRECTIVE}
        if unknown:
            raise ValueError(
                f"{ALLOW_FILE}: unknown rule(s) for {path}: {sorted(unknown)}"
            )
        entries[path] = rules
    return entries


def tracked_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def is_text(data: bytes) -> bool:
    return b"\x00" not in data[:TEXT_CHUNK]


def plates_section_lines(lines: list[str]) -> set[int]:
    """1-based line numbers strictly between one balanced marker pair.

    Exactly one begin and one end, begin before end, waive the lines
    between them (marker lines stay strict). Anything else waives
    nothing (fail closed).
    """
    begins = [n for n, line in enumerate(lines, 1) if PLATES_SECTION_BEGIN in line]
    ends = [n for n, line in enumerate(lines, 1) if PLATES_SECTION_END in line]
    if len(begins) != 1 or len(ends) != 1 or begins[0] >= ends[0]:
        return set()
    return set(range(begins[0] + 1, ends[0]))


def scan_lines(
    relpath: str,
    lines: Iterable[str],
    skip_rules: set[str],
    allow_plates_section: bool = False,
) -> list[Finding]:
    """Scan normalised text lines; used for files and generated content."""
    materialised = list(lines)
    waived = plates_section_lines(materialised) if allow_plates_section else set()
    findings = []
    for lineno, line in enumerate(materialised, 1):
        skipped = skip_rules | (PLATES_SECTION_RULES if lineno in waived else set())
        normalised = normalize_line(line)
        for rule, pattern in RULES.items():
            if rule not in skipped and pattern.search(normalised):
                findings.append(Finding(relpath, lineno, rule))
    return findings


def scan_file(
    root: Path, relpath: str, skip_rules: set[str], allow_plates_section: bool = False
) -> list[Finding]:
    try:
        data = (root / relpath).read_bytes()
    except OSError:
        return []
    if not is_text(data):
        return []
    return scan_lines(
        relpath,
        data.decode("utf-8", errors="replace").splitlines(),
        skip_rules,
        allow_plates_section,
    )


def scan(root: Path) -> list[Finding]:
    allow = load_allowlist(root)
    findings: list[Finding] = []
    for relpath in tracked_files(root):
        if relpath.startswith(".git/"):
            continue
        if relpath in allow and not allow[relpath]:
            continue
        granted = allow.get(relpath, set())
        findings.extend(
            scan_file(
                root,
                relpath,
                granted & set(RULES),
                SECTION_DIRECTIVE in granted,
            )
        )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="parking-stats-privacy-scan",
        description="Fail when tracked files contain plates, phone numbers "
        "or raw-export keys (aggregate-only rule).",
    )
    parser.add_argument("root", type=Path, nargs="?", default=Path("."))
    args = parser.parse_args(argv)
    findings = scan(args.root)
    for finding in findings:
        print(str(finding), file=sys.stderr)
    if findings:
        print(f"privacy scan: {len(findings)} finding(s)", file=sys.stderr)
        return 1
    print("privacy scan: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
