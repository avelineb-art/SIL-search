"""Organisation identity extraction and name normalisation.

Website trading names and legal entity names commonly differ (e.g. a site
branded "Sunrise Living" trading under "Sunrise Community Services Pty
Ltd"). This module pulls candidate names from several sources - page
title/H1, structured data, footer copyright, and privacy/complaints-page
text - and keeps them as separate, labelled candidates rather than
collapsing them into one guess. `ProviderRecord.trading_name` and
`legal_name` are populated by the caller choosing among these candidates;
ABN Lookup (Stage 3) is the authoritative source for the legal name
whenever an ABN can be confirmed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_COMPANY_SUFFIXES = [
    "Proprietary Limited",
    "Pty Ltd",
    "Pty. Ltd.",
    "Pty Limited",
    "Limited",
    "Ltd",
    "Incorporated",
    "Inc",
]

# Every word before the suffix must itself start with a capital letter -
# i.e. this only matches proper-noun-looking runs (e.g. "Sunrise Living")
# immediately before a company suffix, not an arbitrary sentence fragment
# that merely happens to end in one (e.g. "operated by Sunrise Living").
_LEGAL_NAME_PATTERN = re.compile(
    r"((?:[A-Z][\w&'.\-]*\s+){1,6}(?:"
    + "|".join(re.escape(s) for s in _COMPANY_SUFFIXES)
    + r"))\b"
)

_COPYRIGHT_PATTERN = re.compile(
    r"(?:©|\(c\)|copyright)\s*\d{4}(?:\s*[–\-]\s*\d{2,4})?\s+([A-Z][A-Za-z0-9&'.,\- ]{2,80}?)"
    r"(?=\.|,|\ball rights reserved\b|\n|$)",
    re.IGNORECASE,
)

# Suffixes that may optionally be stripped for *matching* purposes only -
# never applied to the name actually displayed to a reviewer.
_STRIPPABLE_SUFFIXES = [
    "proprietary limited",
    "pty ltd",
    "pty limited",
    "limited",
    "ltd",
    "incorporated",
    "inc",
    "australia",
    "group",
    "disability services",
    "support services",
    "services",
    "care",
]

_PUNCTUATION_PATTERN = re.compile(r"[^\w\s&]")
_WHITESPACE_PATTERN = re.compile(r"\s+")


@dataclass(frozen=True)
class NameCandidate:
    name: str
    source: str  # title | heading | structured_data | footer_copyright | privacy_policy | complaints_policy


def find_legal_name_candidates(text: str, source: str) -> list[NameCandidate]:
    seen: set[str] = set()
    candidates: list[NameCandidate] = []
    for match in _LEGAL_NAME_PATTERN.finditer(text):
        name = " ".join(match.group(1).split())
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        candidates.append(NameCandidate(name=name, source=source))
    return candidates


def find_copyright_name(footer_text: str) -> str | None:
    match = _COPYRIGHT_PATTERN.search(footer_text)
    if not match:
        return None
    return " ".join(match.group(1).split()).rstrip(".,")


def normalise_name(name: str, strip_suffixes: bool = False) -> str:
    """Normalise a business name for matching.

    Lower-cases, standardises "&"/"and", strips punctuation, collapses
    whitespace, and - only when `strip_suffixes` is True - removes common
    trailing suffixes (Pty Ltd, Group, Services, etc.). The caller should
    always retain the original alongside the normalised form; stripping too
    many suffixes can make genuinely different organisations collide, so
    `strip_suffixes` is opt-in and should be used for fuzzy-matching
    candidate generation, not as the sole comparison.
    """
    value = name.strip().lower()
    value = re.sub(r"\s*&\s*", " and ", value)
    value = _PUNCTUATION_PATTERN.sub(" ", value)
    value = _WHITESPACE_PATTERN.sub(" ", value).strip()

    if strip_suffixes:
        for suffix in sorted(_STRIPPABLE_SUFFIXES, key=len, reverse=True):
            pattern = re.compile(rf"\b{re.escape(suffix)}\b\.?$")
            new_value = pattern.sub("", value).strip()
            new_value = _WHITESPACE_PATTERN.sub(" ", new_value).strip()
            if new_value:
                value = new_value

    return value
