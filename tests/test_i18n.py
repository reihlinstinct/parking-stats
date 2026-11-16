"""Language catalog tests (td423/td424): the catalog is the only language source.

Every language comes from ``parking_stats.catalog``; nothing here names a
language code, so adding a language block plus its values is the whole
change these tests ask for.
"""

from __future__ import annotations

import ast
import json
import re
import string
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

from parking_stats import catalog, heatmap, i18n
from parking_stats.analytics import add_analytics
from parking_stats.charts import hbar_chart, vbar_chart
from parking_stats.publish import build_index, publish

ROOT = Path(__file__).resolve().parents[1]
CODES = [block["code"] for block in catalog.LANGUAGES]
DEFAULT = [block["code"] for block in catalog.LANGUAGES if block["default"]]
HEBREW = re.compile("[\u0590-\u05ff]")

# Files allowed to name a language code. The catalog is the source; no
# other file is exempt.
CODE_SCAN_ALLOW = {"src/parking_stats/catalog.py"}
# Files allowed to carry Hebrew string literals outside docstrings: the
# catalog itself, and union.py, whose character class validates the
# city's own category and street labels (data, never UI text).
HEBREW_SCAN_ALLOW = {"src/parking_stats/catalog.py", "src/parking_stats/union.py"}
SOURCE_DIRS = ("src", "scripts", "tests", "tools")


def _fields(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def _forms(value: str | dict[str, str]) -> list[str]:
    return list(value.values()) if isinstance(value, dict) else [value]


def _sources() -> list[tuple[str, ast.AST]]:
    found = []
    for top in SOURCE_DIRS:
        for path in sorted((ROOT / top).rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            found.append((rel, ast.parse(path.read_text(encoding="utf-8"))))
    return found


def _docstrings(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def hardcoded_language_codes(
    sources: list[tuple[str, ast.AST]], codes: list[str]
) -> list[str]:
    """String literals naming a catalog language outside the allow-list."""
    attr = re.compile(
        r"""\b(?:hreflang|lang|xml:lang)\s*=\s*["']?(%s)\b"""
        % "|".join(map(re.escape, codes))
    )
    page = re.compile(r"\.(%s)\.html\b" % "|".join(map(re.escape, codes)))
    hits = []
    for rel, tree in sources:
        if rel in CODE_SCAN_ALLOW:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
                if text in codes or attr.search(text) or page.search(text):
                    hits.append(f"{rel}:{node.lineno}: {text[:60]!r}")
    return hits


def hebrew_literals(sources: list[tuple[str, ast.AST]]) -> list[str]:
    hits = []
    for rel, tree in sources:
        if rel in HEBREW_SCAN_ALLOW or not rel.startswith("src/"):
            continue
        skip = _docstrings(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in skip
                and HEBREW.search(node.value)
            ):
                hits.append(f"{rel}:{node.lineno}: {node.value[:40]!r}")
    return hits


class PickerParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_nav = 0
        self.links: list[dict[str, str | None]] = []
        self.texts: list[str] = []
        self.html_attrs: dict[str, str | None] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "html":
            self.html_attrs = a
        if tag == "nav" and a.get("class") == "langs":
            self.in_nav += 1
        elif tag == "a" and self.in_nav:
            self.links.append(a)
            self.texts.append("")

    def handle_endtag(self, tag: str) -> None:
        if tag == "nav" and self.in_nav:
            self.in_nav -= 1

    def handle_data(self, data: str) -> None:
        if self.in_nav and self.texts:
            self.texts[-1] += data


def parse(page: str) -> PickerParser:
    parser = PickerParser()
    parser.feed(page)
    return parser


FILES = {
    "summary.json": json.dumps(
        {
            "total_incidents": 1234,
            "by_month": {"2026-05": 4, "2026-06": 9},
            "date_range": {"first": "2026-01-03", "last": "2026-06-30"},
            "closure": {"median_days": 2.5},
        }
    ),
    "monthly.csv": "month,reports\n2026-05,4\n2026-06,9\n",
    "categories.csv": "category,reports\nחניה אסורה,9\n",
    "streets.csv": "street,reports\nרחוב הדוגמה,8\n",
    "report.html": "<html><head></head><body>דוח</body></html>\n",
}


def make_input() -> Path:
    root = Path(tempfile.mkdtemp())
    for name, text in FILES.items():
        (root / name).write_text(text, encoding="utf-8")
    return root


class CatalogShapeTest(unittest.TestCase):
    def test_language_blocks_are_complete(self) -> None:
        self.assertGreaterEqual(len(CODES), 1)
        self.assertEqual(len(CODES), len(set(CODES)), "duplicate language code")
        for block in catalog.LANGUAGES:
            self.assertEqual(
                set(block), set(i18n.LANGUAGE_FIELDS), f"fields of {block['code']}"
            )
            self.assertRegex(block["code"], r"^[a-z]{2,3}$")
            self.assertTrue(block["name"].strip(), f"endonym of {block['code']}")
            self.assertIn(block["dir"], ("ltr", "rtl"))
            self.assertIn(block["plural"], i18n.PLURAL_FAMILIES)

    def test_exactly_one_default_and_drafts_are_flagged(self) -> None:
        self.assertEqual(len(DEFAULT), 1, "exactly one default language")
        for block in catalog.LANGUAGES:
            if block["default"]:
                self.assertFalse(block["machine_draft"], "the source is no draft")
            else:
                self.assertTrue(
                    block["machine_draft"],
                    f"{block['code']}: a non-source language stays a machine "
                    "draft until a native review clears the flag",
                )

    def test_every_language_has_every_key(self) -> None:
        missing = sorted(
            f"{key}: {code}"
            for key, values in catalog.STRINGS.items()
            for code in CODES
            if code not in values
        )
        self.assertEqual(missing, [], "keys missing a language")
        stray = sorted(
            f"{key}: {code}"
            for key, values in catalog.STRINGS.items()
            for code in values
            if code not in CODES
        )
        self.assertEqual(stray, [], "values for a language with no block")

    def test_values_non_empty_with_matching_placeholders(self) -> None:
        source = DEFAULT[0]
        for key, values in catalog.STRINGS.items():
            reference = values[source]
            ref_fields = set().union(*(_fields(f) for f in _forms(reference)))
            for code, value in values.items():
                self.assertIs(
                    type(value), type(reference), f"{key}: {code} plural shape"
                )
                for form in _forms(value):
                    self.assertTrue(form.strip(), f"{key}: {code} is empty")
                fields = set().union(*(_fields(f) for f in _forms(value)))
                self.assertEqual(fields, ref_fields, f"{key}: {code} placeholders")

    def test_plural_values_carry_their_family_categories(self) -> None:
        family = {block["code"]: block["plural"] for block in catalog.LANGUAGES}
        for key, values in catalog.STRINGS.items():
            for code, value in values.items():
                if isinstance(value, dict):
                    self.assertEqual(
                        set(value),
                        set(i18n.PLURAL_FAMILIES[family[code]].categories),
                        f"{key}: {code}",
                    )

    def test_plural_families_choose_categories(self) -> None:
        for name, fam in i18n.PLURAL_FAMILIES.items():
            for n in range(0, 30):
                self.assertIn(fam.select(n), fam.categories, f"{name}({n})")


class DiscoveryTest(unittest.TestCase):
    def test_languages_come_from_the_catalog(self) -> None:
        self.assertEqual(i18n.codes(), CODES)
        self.assertEqual(i18n.default_code(), DEFAULT[0])
        for code in CODES:
            self.assertEqual(i18n.language(code)["code"], code)
        with self.assertRaises(KeyError):
            i18n.language("zz-not-a-language")

    def test_page_names(self) -> None:
        names = [i18n.page_name("index", code) for code in CODES]
        self.assertEqual(len(set(names)), len(CODES))
        self.assertEqual(i18n.page_name("index", DEFAULT[0]), "index.html")
        for code in CODES:
            self.assertTrue(names[CODES.index(code)].endswith(".html"))

    def test_lookup_formats_and_pluralises(self) -> None:
        for code in CODES:
            fam = i18n.PLURAL_FAMILIES[i18n.language(code)["plural"]]
            for n in (0, 1, 2, 5):
                text = i18n.tn("heatmap.note.unmapped", n, code)
                form = catalog.STRINGS["heatmap.note.unmapped"][code]
                assert isinstance(form, dict)
                self.assertEqual(text, form[fam.select(n)].format(count=n))
        with self.assertRaises(KeyError):
            i18n.t("no.such.key")


class PickerTest(unittest.TestCase):
    """Every page shows exactly the catalog languages, by endonym."""

    def _pages(self, code: str) -> dict[str, str]:
        cells, below, unmapped = heatmap.build_cells(
            {"א": 9, "ג": 4}, {"א": (31.7704, 35.2104), "ג": (31.7654, 35.2092)}
        )
        return {
            "index": build_index(
                make_input(), "label", "now", heatmap_available=True, lang=code
            ),
            "heatmap": heatmap.build_heatmap_html(
                cells, unmapped, below, "now", lang=code
            ),
        }

    def test_picker_lists_exactly_the_catalog_languages(self) -> None:
        if len(CODES) < 2:
            self.skipTest("a single language needs no picker")
        for code in CODES:
            for base, page in self._pages(code).items():
                parsed = parse(page)
                where = f"{base} in {code}"
                self.assertEqual(
                    [a.get("hreflang") for a in parsed.links], CODES, where
                )
                self.assertEqual(
                    [t.strip() for t in parsed.texts],
                    [block["name"] for block in catalog.LANGUAGES],
                    where,
                )
                self.assertEqual(
                    [a.get("href") for a in parsed.links],
                    [i18n.page_name(base, c) for c in CODES],
                    where,
                )
                self.assertEqual([a.get("lang") for a in parsed.links], CODES, where)
                current = [
                    a.get("hreflang") for a in parsed.links if "aria-current" in a
                ]
                self.assertEqual(current, [code], where)
                for block, a in zip(catalog.LANGUAGES, parsed.links):
                    self.assertEqual(a.get("dir"), block["dir"], where)
                for c in CODES:
                    self.assertIn(
                        f'<link rel="alternate" hreflang="{c}" '
                        f'href="{i18n.page_name(base, c)}">',
                        page,
                        where,
                    )

    def test_page_language_and_direction_follow_the_catalog(self) -> None:
        for block in catalog.LANGUAGES:
            code = block["code"]
            for base, page in self._pages(code).items():
                attrs = parse(page).html_attrs
                self.assertEqual(attrs.get("lang"), code, base)
                self.assertEqual(attrs.get("dir"), block["dir"], base)
                title = "site.title" if base == "index" else "heatmap.title"
                self.assertIn(f"<title>{i18n.t(title, code)}</title>", page, base)
                draft_note = i18n.t("machine_draft.notice", code)
                self.assertEqual(draft_note in page, block["machine_draft"], base)

    def test_links_stay_within_the_language(self) -> None:
        for code in CODES:
            pages = self._pages(code)
            self.assertIn(f'href="{i18n.page_name("heatmap", code)}"', pages["index"])
            self.assertIn(f'href="{i18n.page_name("index", code)}"', pages["heatmap"])


class NotTranslatedTest(unittest.TestCase):
    """Street, category and plate data and the data files stay as published."""

    def test_data_values_are_unchanged_in_every_language(self) -> None:
        for code in CODES:
            page = build_index(make_input(), "label", "now", lang=code)
            self.assertIn("חניה אסורה", page, code)
            self.assertIn("רחוב הדוגמה", page, code)
            self.assertIn('href="data/summary.json"', page, code)
            self.assertIn('href="data/categories.csv"', page, code)
            self.assertIn('href="report.html"', page, code)

    def test_charts_translate_only_their_labels(self) -> None:
        for code in CODES:
            svg = hbar_chart([("רחוב", 3)], lang=code)
            self.assertIn(">רחוב</text>", svg)
            self.assertIn(i18n.tn("chart.rows", 1, code), svg)
            self.assertIn(
                i18n.tn("chart.bars", 1, code), vbar_chart([("x", 1)], lang=code)
            )


class PublishAllLanguagesTest(unittest.TestCase):
    def test_publish_writes_one_page_set_per_language(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            public = Path(tmp) / "public"
            mapping = Path(tmp) / "mapping.json"
            mapping.write_text(json.dumps({"רחוב הדוגמה": [31.7704, 35.2104]}))
            summary = json.loads(FILES["summary.json"])
            summary["by_street"] = {"רחוב הדוגמה": 8}
            src = make_input()
            (src / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
            self.assertEqual(publish(src, public, None, geocoding=mapping), 0)
            for base in ("index", "heatmap"):
                written = sorted(
                    p.name
                    for p in public.glob(f"{base}*.html")
                    if p.name.split(".")[0] == base
                )
                self.assertEqual(
                    written, sorted(i18n.page_name(base, c) for c in CODES), base
                )
            for code in CODES:
                page = (public / i18n.page_name("index", code)).read_text(
                    encoding="utf-8"
                )
                self.assertIn(i18n.t("label.default", code), page, code)
                self.assertIn(i18n.t("analytics.privacy", code), page, code)
                self.assertEqual(page.count("cloudflareinsights.com/beacon"), 1)
            # The upstream report keeps its own (Hebrew) disclosure.
            report = (public / "report.html").read_text(encoding="utf-8")
            self.assertIn(i18n.t("analytics.privacy", DEFAULT[0]), report)
            # Without the mapping every language's heat map goes.
            self.assertEqual(publish(src, public, None), 0)
            for code in CODES:
                self.assertFalse((public / i18n.page_name("heatmap", code)).exists())

    def test_operator_label_is_used_in_every_language(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            public = Path(tmp) / "public"
            self.assertEqual(publish(make_input(), public, "תווית המפעיל"), 0)
            for code in CODES:
                page = (public / i18n.page_name("index", code)).read_text(
                    encoding="utf-8"
                )
                self.assertIn("<bdi>תווית המפעיל</bdi>", page, code)

    def test_add_analytics_uses_the_page_language(self) -> None:
        for code in CODES:
            page = add_analytics("<html><head></head><body></body></html>", lang=code)
            self.assertIn(i18n.t("analytics.privacy", code), page)


class HardcodedLanguageScanTest(unittest.TestCase):
    """Tripwire: no language code and no UI text outside the catalog."""

    def test_no_language_code_outside_the_catalog(self) -> None:
        self.assertEqual(hardcoded_language_codes(_sources(), CODES), [])

    def test_scan_catches_a_planted_code(self) -> None:
        planted = [("src/x.py", ast.parse(f"X = {CODES[0]!r}\n"))]
        self.assertEqual(len(hardcoded_language_codes(planted, CODES)), 1)
        planted = [("src/x.py", ast.parse(f"X = '<html lang=\"{CODES[0]}\">'\n"))]
        self.assertEqual(len(hardcoded_language_codes(planted, CODES)), 1)
        allowed = [("src/parking_stats/catalog.py", ast.parse(f"X = {CODES[0]!r}\n"))]
        self.assertEqual(hardcoded_language_codes(allowed, CODES), [])

    def test_no_hebrew_ui_text_outside_the_catalog(self) -> None:
        self.assertEqual(hebrew_literals(_sources()), [])


if __name__ == "__main__":
    unittest.main()
