"""Shared building blocks for the rule-based classifiers: a common
`TextSource` shape (one page's, or one PDF page's, worth of text plus
provenance), phrase matching, excerpt generation, and evidence hashing.

Kept generic and dependency-free so both `sil_classifier` and
`registration_classifier` build on the exact same, easily-testable matching
primitives - the only thing that differs between them is which phrases and
points they apply.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

_EMPLOYMENT_KEYWORDS = ("career", "careers", "job", "jobs", "vacanc", "employment", "recruit")
_BLOG_KEYWORDS = ("/blog", "blog/", "/news", "news/", "/article", "article/", "/insights")


@dataclass
class TextSource:
    """One unit of extracted text a classifier can score, e.g. a page's
    visible text, its metadata, its structured data, or one page of a PDF.
    """

    text: str
    source_url: str
    origin: str  # VISIBLE_TEXT | METADATA | STRUCTURED_DATA | PDF_TEXT
    page_title: str | None = None
    page_type: str = "general"  # general | employment | blog
    document_page: int | None = None


@dataclass(frozen=True)
class PhraseMatch:
    start: int
    end: int
    matched_text: str


def classify_page_type(url: str, title: str | None = None) -> str:
    haystack = f"{url} {title or ''}".lower()
    if any(keyword in haystack for keyword in _EMPLOYMENT_KEYWORDS):
        return "employment"
    if any(keyword in haystack for keyword in _BLOG_KEYWORDS):
        return "blog"
    return "general"


def find_phrase_occurrences(text: str, phrase: str) -> list[PhraseMatch]:
    lowered = text.lower()
    needle = phrase.lower()
    if not needle:
        return []
    matches: list[PhraseMatch] = []
    start = 0
    while True:
        idx = lowered.find(needle, start)
        if idx == -1:
            break
        matches.append(PhraseMatch(start=idx, end=idx + len(needle), matched_text=text[idx : idx + len(needle)]))
        start = idx + len(needle)
    return matches


def has_nearby_any(text: str, start: int, end: int, keywords: list[str], window_chars: int) -> bool:
    lowered = text.lower()
    lo = max(0, start - window_chars)
    hi = min(len(text), end + window_chars)
    window_text = lowered[lo:hi]
    return any(keyword.lower() in window_text for keyword in keywords)


def make_excerpt(text: str, start: int, end: int, radius: int = 120) -> str:
    lo = max(0, start - radius)
    hi = min(len(text), end + radius)
    excerpt = text[lo:hi].strip()
    prefix = "…" if lo > 0 else ""
    suffix = "…" if hi < len(text) else ""
    return f"{prefix}{excerpt}{suffix}"


def evidence_hash(provider_domain: str, evidence_category: str, matched_text: str, source_url: str) -> str:
    raw = f"{provider_domain}|{evidence_category}|{matched_text}|{source_url}".lower()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def now() -> datetime:
    return datetime.utcnow()
