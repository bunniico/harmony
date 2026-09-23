"""Input cleaning: Unicode normalization, zero-width stripping, tag escaping."""

from __future__ import annotations

import re
import unicodedata

MAX_INPUT_CHARS = 2000

_ZERO_WIDTH = re.compile("[­᠎​-‏‪-‮⁠-⁤⁦-⁩﻿]")

# Tag names the bot uses in its own prompt. Users must not be able to forge or close them.
_RESERVED_TAGS = r"msg|system|memory|directive|context|rules|persona|summary|canary"
_TAG = re.compile(rf"<(\s*/?\s*(?:{_RESERVED_TAGS})\b)", re.IGNORECASE)


def normalize(text: str) -> str:
    return _ZERO_WIDTH.sub("", unicodedata.normalize("NFKC", text))


# Common Cyrillic/Greek lookalikes that NFKC leaves alone. Used for detection only.
_CONFUSABLES = str.maketrans(
    "аеорсухіјѕԁɡһӏԛԝАВЕКМНОРСТХІЈЅαοντικΑΒΕΗΙΚΜΝΟΡΤΧΥΖ",
    "aeopcyxijsdghlqwABEKMHOPCTXIJSaovtikABEHIKMNOPTXYZ",
)


def skeleton(text: str) -> str:
    """Lowercased, normalized, lookalike-folded form for pattern matching."""
    return normalize(text).translate(_CONFUSABLES).lower()


def escape_tags(text: str) -> str:
    # Match on the lookalike-folded text (same length, 1:1 chars) so "<mѕg" is caught too.
    folded = text.translate(_CONFUSABLES)
    starts = {m.start() for m in _TAG.finditer(folded)}
    return "".join("‹" if i in starts else ch for i, ch in enumerate(text))


def escape_attr(value: str) -> str:
    """For values placed inside a code-generated header attribute."""
    value = normalize(value)
    return (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", " ")
    )[:64]


def clean(text: str, max_chars: int = MAX_INPUT_CHARS) -> str:
    text = escape_tags(normalize(text)).strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "…"
    return text
