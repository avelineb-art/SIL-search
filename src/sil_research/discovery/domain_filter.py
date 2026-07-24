"""Canonical-domain deduplication and discovery-result exclusion.

Applied to every raw SearchResult before it becomes a provider candidate.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

import tldextract

from sil_research.config import get_app_settings

# `suffix_list_urls=()` pins tldextract to its bundled public-suffix-list
# snapshot and disables the default behaviour of fetching a fresh copy from
# the internet on first use - this tool must not make surprise outbound
# calls to a third party just to canonicalise a domain, and must keep
# working in network-restricted environments.
_EXTRACTOR = tldextract.TLDExtract(suffix_list_urls=())


def canonical_domain(url: str) -> str:
    """Return the registrable domain (no scheme, no `www.`, no path)."""
    extracted = _EXTRACTOR(url)
    if not extracted.domain or not extracted.suffix:
        # Fall back to a plain host parse for anything tldextract can't
        # classify (e.g. a malformed or non-standard URL).
        host = urlparse(url).netloc or url
        return host.lower().removeprefix("www.")
    return f"{extracted.domain}.{extracted.suffix}".lower()


@dataclass(frozen=True)
class DomainDecision:
    domain: str
    excluded: bool
    reason: str | None = None
    is_job_board: bool = False
    is_generic_directory: bool = False


def classify_domain(url: str) -> DomainDecision:
    """Decide whether a discovery-result domain should be excluded outright,
    or flagged as a job board / generic directory (from which only the
    employer name and website are extracted - the listing domain itself is
    never treated as a provider website).
    """
    settings = get_app_settings().get("discovery", {})
    domain = canonical_domain(url)

    excluded_suffixes = settings.get("excluded_domain_suffixes", [])
    excluded_keywords = settings.get("excluded_domain_keywords", [])
    job_board_domains = settings.get("job_board_domains", [])
    directory_domains = settings.get("generic_directory_domains", [])

    if domain in job_board_domains:
        return DomainDecision(domain=domain, excluded=False, is_job_board=True)
    if domain in directory_domains:
        return DomainDecision(domain=domain, excluded=False, is_generic_directory=True)

    for suffix in excluded_suffixes:
        if domain == suffix or domain.endswith(f".{suffix}"):
            return DomainDecision(domain=domain, excluded=True, reason=f"social/media domain ({suffix})")

    for keyword in excluded_keywords:
        if keyword in domain:
            return DomainDecision(domain=domain, excluded=True, reason=f"excluded domain keyword ({keyword})")

    if url.lower().endswith(".pdf"):
        return DomainDecision(domain=domain, excluded=True, reason="PDF-only result, no provider website identified")

    return DomainDecision(domain=domain, excluded=False)


def dedupe_by_domain(urls: list[str]) -> dict[str, str]:
    """Return a mapping of canonical_domain -> first-seen URL for that domain."""
    seen: dict[str, str] = {}
    for url in urls:
        domain = canonical_domain(url)
        seen.setdefault(domain, url)
    return seen
