"""Canonical-URL normalisation and crawl-order prioritisation.

Keeps the crawler from re-fetching duplicate URL variants of the same page,
and makes sure the limited per-domain page budget is spent on the pages
most likely to carry SIL or registration evidence.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sil_research.config import get_app_settings
from sil_research.crawling.page_parser import ParsedLink

_TRACKING_PARAM_PREFIXES = ("utm_", "gclid", "fbclid", "mc_cid", "mc_eid")


def normalise_url(url: str) -> str:
    """Collapse trivial URL variants (scheme case, default ports, trailing
    slash, tracking query params, fragment, query-param order) to a single
    canonical form so the same logical page is never crawled twice.
    """
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    if netloc.endswith(":80") and scheme == "http":
        netloc = netloc[: -len(":80")]
    if netloc.endswith(":443") and scheme == "https":
        netloc = netloc[: -len(":443")]

    path = parsed.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    kept_params = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if not any(k.lower().startswith(prefix) for prefix in _TRACKING_PARAM_PREFIXES)
    ]
    kept_params.sort()
    query = urlencode(kept_params)

    return urlunparse((scheme, netloc, path, "", query, ""))


def seed_paths() -> list[str]:
    return get_app_settings().get("crawler", {}).get("priority_paths", [])


def link_keyword_triggers() -> list[str]:
    return get_app_settings().get("crawler", {}).get("link_keyword_triggers", [])


def seed_urls(base_url: str) -> list[str]:
    base = base_url.rstrip("/")
    urls = []
    for path in seed_paths():
        if path == "/":
            urls.append(f"{base}/")
        else:
            urls.append(f"{base}/{path.lstrip('/')}")
    return urls


def link_priority_score(link: ParsedLink) -> int:
    """Higher score = crawl sooner. Based on keyword hits in the URL path
    and anchor text against the configurable trigger-word list.
    """
    haystack = f"{link.url.lower()} {link.anchor_text.lower()}"
    return sum(1 for keyword in link_keyword_triggers() if keyword in haystack)


def order_links_by_priority(links: list[ParsedLink]) -> list[ParsedLink]:
    return sorted(links, key=link_priority_score, reverse=True)
