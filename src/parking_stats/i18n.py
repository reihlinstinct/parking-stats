"""Lookups over the language catalog (catalog.py), the only language source.

Nothing here names a language: the list, the default, the direction, the
picker and the page names all come from ``catalog.LANGUAGES``.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from dataclasses import dataclass

from .catalog import LANGUAGES, STRINGS, Language

LANGUAGE_FIELDS = tuple(Language.__annotations__)


@dataclass(frozen=True)
class PluralFamily:
    """A plural rule: which category a count takes, and the categories."""

    select: Callable[[int], str]
    categories: tuple[str, ...]


# Named rule families; a language block picks one with its ``plural``
# field. A new language with a new rule adds a family here.
PLURAL_FAMILIES: dict[str, PluralFamily] = {
    # 1 is singular, everything else (0 included) plural.
    "one_is_1": PluralFamily(lambda n: "one" if n == 1 else "other", ("one", "other")),
    # 0 and 1 are singular.
    "one_is_0_1": PluralFamily(
        lambda n: "one" if n in (0, 1) else "other", ("one", "other")
    ),
}


def codes() -> list[str]:
    """Every catalog language code, in catalog order."""
    return [block["code"] for block in LANGUAGES]


def default_code() -> str:
    """The source language: the one block marked ``default``."""
    return next(block["code"] for block in LANGUAGES if block["default"])


def language(code: str | None = None) -> Language:
    """The catalog block for ``code`` (the default language for None)."""
    wanted = default_code() if code is None else code
    for block in LANGUAGES:
        if block["code"] == wanted:
            return block
    raise KeyError(f"language not in the catalog: {wanted!r}")


def _value(key: str, code: str) -> str | dict[str, str]:
    values = STRINGS[key]
    # The tests keep every key complete; the default is only a safety net.
    return values[code] if code in values else values[default_code()]


def t(key: str, lang: str | None = None, **params: object) -> str:
    """The catalog text for ``key`` in ``lang``, with params filled in."""
    code = language(lang)["code"]
    value = _value(key, code)
    if isinstance(value, dict):
        raise TypeError(f"{key} is a counted string; use tn()")
    return value.format(**params) if params else value


def tn(key: str, count: int, lang: str | None = None, **params: object) -> str:
    """The counted catalog text for ``key``: the plural form for ``count``."""
    block = language(lang)
    value = _value(key, block["code"])
    if not isinstance(value, dict):
        raise TypeError(f"{key} is not a counted string; use t()")
    form = value[PLURAL_FAMILIES[block["plural"]].select(count)]
    return form.format(count=count, n=count, **params)


def page_name(base: str, lang: str | None = None) -> str:
    """``index`` -> ``index.html`` for the default language, else
    ``index.<code>.html``: the same folder, so relative links still work."""
    code = language(lang)["code"]
    return f"{base}.html" if code == default_code() else f"{base}.{code}.html"


def html_attrs(lang: str | None = None) -> str:
    """The ``lang`` and ``dir`` attributes for a page's <html> element."""
    block = language(lang)
    return f'lang="{block["code"]}" dir="{block["dir"]}"'


def alternate_links(base: str) -> str:
    """<link rel="alternate"> for every catalog language of page ``base``."""
    if len(LANGUAGES) < 2:
        return ""
    return "".join(
        f'<link rel="alternate" hreflang="{block["code"]}" '
        f'href="{page_name(base, block["code"])}">'
        for block in LANGUAGES
    )


def picker(base: str, lang: str | None = None) -> str:
    """The language picker: one link per catalog language, by endonym."""
    if len(LANGUAGES) < 2:
        return ""
    current = language(lang)["code"]
    items = []
    for block in LANGUAGES:
        code = block["code"]
        mark = ' aria-current="page"' if code == current else ""
        items.append(
            f'<li><a href="{page_name(base, code)}" hreflang="{code}" '
            f'lang="{code}" dir="{block["dir"]}"{mark}>'
            f"{html.escape(block['name'])}</a></li>"
        )
    return (
        f'<nav class="langs" aria-label="{html.escape(t("picker.label", current))}">'
        f"<ul>{''.join(items)}</ul></nav>"
    )


def draft_notice(lang: str | None = None) -> str:
    """A visible note on machine-draft pages; empty for reviewed text."""
    block = language(lang)
    if not block["machine_draft"]:
        return ""
    return f'<p class="draft-note">{t("machine_draft.notice", block["code"])}</p>'


PICKER_CSS = (
    "nav.langs ul { list-style: none; margin: .6rem 0 0; padding: 0;"
    " display: flex; flex-wrap: wrap; justify-content: center; gap: .4rem; }\n"
    "nav.langs a { color: inherit; padding: .1rem .5rem;"
    " border: 1px solid currentColor; border-radius: .4rem;"
    " text-decoration: none; font-size: .9rem; }\n"
    'nav.langs a[aria-current="page"] { font-weight: 700; }\n'
    ".draft-note { font-size: .85rem; opacity: .9; }\n"
)
