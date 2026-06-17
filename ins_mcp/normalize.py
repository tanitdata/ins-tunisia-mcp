"""Text normalization for INS/Prognoz responses.

Names and descriptions arrive dirty: trailing tabs (`&#x9;`), embedded `<br>`,
HTML entities, and stray combining characters (a kasra sits inside "Cِette" in
the C_NSO description). Every human-readable string is cleaned on ingest.
Identity is always `@Id`/`@Key`, never these cleaned names.
"""

from __future__ import annotations

import html
import re
import unicodedata

_BR = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)
_WS = re.compile(r"\s+")

# Arabic combining diacritics (harakat) that turn up mojibake'd inside French
# words. Stripping standalone combining marks is safe for the Latin labels here.
_STRAY_COMBINING = re.compile(r"[ً-ْٰ]")


def clean_text(value: str | None) -> str:
    """Normalize a single human-readable string. Returns '' for None/empty."""
    if not value:
        return ""
    text = html.unescape(value)
    text = _BR.sub(" ", text)
    # A second unescape: some labels are double-encoded (&amp;#x9;).
    if "&" in text:
        text = html.unescape(text)
    text = _STRAY_COMBINING.sub("", text)
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\t", " ").replace("\xa0", " ")
    text = _WS.sub(" ", text)
    return text.strip()


def fold(value: str | None) -> str:
    """Lower-case and strip accents for tolerant search matching.

    French labels here mix accented/unaccented spellings, and users on non-French
    keyboards routinely type without accents — so "utilisee" must match "utilisée".
    """
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    no_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
    return no_marks.casefold().strip()
