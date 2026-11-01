"""Tests for the privacy scanner. Synthetic samples only, built in tmpdirs.

Every sample is built from obviously fake digits, and literals stay
split into short pieces so this file itself passes the scanner; only the
raw-export-key rule is allowlisted here (see .privacy-scan-allow).
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from parking_stats.privacy_scan import RULES, main, normalize_line, scan

PLATE = "12" + "-345-" + "67"
PHONE = "050" + "-1234" + "567"


def make_repo(files: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    for relpath, text in files.items():
        path = root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    return root


class RuleTest(unittest.TestCase):
    def test_plate_and_phone_patterns_match(self):
        self.assertTrue(RULES["plate"].search(PLATE))
        self.assertTrue(RULES["plate"].search("123" + "-45-" + "678"))
        self.assertTrue(RULES["phone"].search(PHONE))
        self.assertFalse(RULES["plate"].search("2026-11-01"))

    def test_raw_export_key_pattern_matches(self):
        self.assertTrue(RULES["raw-export-key"].search('"description": "x"'))
        self.assertTrue(RULES["raw-export-key"].search("'caseId': 1"))
        self.assertFalse(RULES["raw-export-key"].search('"summary": "x"'))

    def test_plate_separator_variants(self):
        self.assertTrue(RULES["plate"].search("12" + " 345 " + "67"))
        self.assertTrue(RULES["plate"].search("12" + "‒345‒" + "67"))
        self.assertTrue(RULES["plate"].search("123" + "—" + "45" + "—" + "678"))
        self.assertTrue(RULES["plate"].search("12.345." + "67"))
        self.assertTrue(RULES["plate"].search("12/345/" + "67"))
        self.assertTrue(RULES["plate"].search("12_345_" + "67"))
        self.assertTrue(RULES["plate"].search("12" + "−345−" + "67"))
        self.assertTrue(RULES["plate"].search("12--345--" + "67"))

    def test_compact_plate_with_stray_separator(self):
        self.assertTrue(RULES["plate-compact"].search("1234" + "567"))
        self.assertTrue(RULES["plate-compact"].search("123" + " 4567"))
        self.assertTrue(RULES["plate-compact"].search("12" + " 34567"))
        self.assertFalse(RULES["plate-compact"].search("2026-11-01"))
        self.assertFalse(RULES["plate-compact"].search("2026-10"))
        self.assertFalse(RULES["plate-compact"].search("727.028"))

    def test_phone_prefix_and_separator_variants(self):
        self.assertTrue(RULES["phone"].search("05012" + "34567"))
        self.assertTrue(RULES["phone"].search("050" + "  123  " + "4567"))
        self.assertTrue(RULES["phone"].search("050.123." + "4567"))
        self.assertTrue(RULES["phone"].search("972" + "5012" + "34567"))
        self.assertTrue(RULES["phone"].search("00" + "972" + "5012" + "34567"))
        self.assertTrue(RULES["phone"].search("+972" + "-50-123-" + "4567"))
        self.assertTrue(RULES["phone"].search("+972 " + "(0)50" + "-123-" + "4567"))
        self.assertTrue(RULES["phone"].search("050" + "‑123‑" + "4567"))
        self.assertFalse(RULES["phone"].search("0500"))

    def test_raw_export_key_variants(self):
        self.assertTrue(RULES["raw-export-key"].search("{caseId: 1}"))
        self.assertTrue(RULES["raw-export-key"].search('"CASEID": 1'))
        self.assertTrue(RULES["raw-export-key"].search("caseId = 1"))
        self.assertTrue(RULES["raw-export-key"].search("{ caseId : 1 }"))
        self.assertTrue(
            RULES["raw-export-column"].search("month," + "description" + ",reports")
        )
        self.assertTrue(
            RULES["raw-export-column"].search("month, " + "description" + " ,reports")
        )
        self.assertFalse(RULES["raw-export-column"].search("street,reports"))

    def test_compact_plate_allows_repeated_separators(self):
        self.assertTrue(RULES["plate-compact"].search("123" + "  " + "4567"))
        self.assertTrue(RULES["plate-compact"].search("12" + " - " + "34567"))
        self.assertFalse(RULES["plate-compact"].search("2026 11 01"))

    def test_bare_mobile_without_prefix(self):
        self.assertTrue(RULES["phone-bare"].search("54" + "1234" + "567"))
        self.assertTrue(RULES["phone-bare"].search("54-123-" + "4567"))
        self.assertFalse(RULES["phone-bare"].search("5412" + "3456"))
        self.assertFalse(RULES["phone-bare"].search("54123" + "45670"))

    def test_raw_export_key_tsv_header(self):
        self.assertTrue(
            RULES["raw-export-column"].search("month\t" + "description" + "\treports")
        )

    def test_normalisation_folds_tricky_unicode(self):
        self.assertTrue(
            RULES["plate"].search(normalize_line("１２" + "-３４" + "５-６７"))
        )
        self.assertTrue(
            RULES["plate"].search(normalize_line("12-3" + "\u200b" + "45-67"))
        )
        self.assertTrue(
            RULES["phone"].search(
                normalize_line("050" + "\u00a0" + "123" + "\u00a0" + "4567")
            )
        )
        self.assertTrue(
            RULES["raw-export-key"].search(
                normalize_line("\ufeff" + '{"description": 1}')
            )
        )


class ScanTest(unittest.TestCase):
    def test_clean_repo_passes(self):
        root = make_repo({"summary.json": '{"2026-10": 42}\n'})
        self.assertEqual(scan(root), [])
        self.assertEqual(main([str(root)]), 0)

    def test_plate_in_tracked_file_fails(self):
        root = make_repo({"data.txt": f"רכב {PLATE} חונה\n"})
        findings = scan(root)
        self.assertEqual(
            [(f.path, f.rule) for f in findings],
            [("data.txt", "plate"), ("data.txt", "plate-compact")],
        )
        self.assertEqual(main([str(root)]), 1)

    def test_raw_export_key_fails(self):
        root = make_repo({"raw.json": '{"incidents": [{"caseId": "x"}]}\n'})
        self.assertEqual([f.rule for f in scan(root)], ["raw-export-key"])

    def test_allowlisted_path_is_skipped(self):
        root = make_repo(
            {
                "samples.txt": f"{PLATE}\n",
                ".privacy-scan-allow": "samples.txt  # synthetic detection samples\n",
            }
        )
        self.assertEqual(scan(root), [])

    def test_rule_scoped_allowlist_waives_only_that_rule(self):
        root = make_repo(
            {
                "samples.txt": f'{PLATE} {{"caseId": "x"}}\n',
                ".privacy-scan-allow": "samples.txt raw-export-key\n",
            }
        )
        self.assertEqual([f.rule for f in scan(root)], ["plate", "plate-compact"])

    def test_unknown_allowlist_rule_is_rejected(self):
        root = make_repo({"a.txt": "ok\n", ".privacy-scan-allow": "a.txt bogus-rule\n"})
        with self.assertRaises(ValueError):
            scan(root)

    def test_untracked_file_is_ignored(self):
        root = make_repo({"ok.txt": "counts only\n"})
        (root / "local_raw.json").write_text(
            f'{{"description": "{PLATE}"}}', encoding="utf-8"
        )
        self.assertEqual(scan(root), [])

    def test_binary_file_is_skipped(self):
        root = make_repo({"ok.txt": "counts\n"})
        (root / "blob.bin").write_bytes(b"\x00\x01" + PLATE.encode())
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        self.assertEqual(scan(root), [])


if __name__ == "__main__":
    unittest.main()
