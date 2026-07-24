"""HTML parsing: pulls out everything the classifiers and extractors need
from a single fetched page, plus the internal links used to drive further
crawling.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser

_WHITESPACE_RE = re.compile(r"\s+")

# Paths that indicate authenticated/participant-only areas or form-only
# functionality - never queued for crawling even if linked internally.
_DISALLOWED_PATH_KEYWORDS = (
    "login",
    "signin",
    "sign-in",
    "logout",
    "wp-admin",
    "admin",
    "my-account",
    "myaccount",
    "account",
    "portal",
    "participant-portal",
    "dashboard",
    "cart",
    "checkout",
)


@dataclass
class ParsedLink:
    url: str
    anchor_text: str


@dataclass
class ParsedPage:
    title: str | None = None
    meta_description: str | None = None
    headings: list[str] = field(default_factory=list)
    visible_text: str = ""
    footer_text: str = ""
    structured_data: list[dict] = field(default_factory=list)
    internal_links: list[ParsedLink] = field(default_factory=list)
    pdf_links: list[str] = field(default_factory=list)


def _clean_text(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()


def is_same_registrable_domain(url: str, base_domain: str) -> bool:
    from sil_research.discovery.domain_filter import canonical_domain

    return canonical_domain(url) == base_domain


def is_disallowed_path(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(keyword in path for keyword in _DISALLOWED_PATH_KEYWORDS)


def parse_page(html: str, page_url: str, base_domain: str) -> ParsedPage:
    tree = HTMLParser(html)
    parsed = ParsedPage()

    title_node = tree.css_first("title")
    if title_node:
        parsed.title = _clean_text(title_node.text())

    meta_desc = tree.css_first('meta[name="description"]')
    if meta_desc and meta_desc.attributes.get("content"):
        parsed.meta_description = _clean_text(meta_desc.attributes["content"])

    for heading_node in tree.css("h1, h2, h3"):
        text = _clean_text(heading_node.text())
        if text:
            parsed.headings.append(text)

    for script_node in tree.css('script[type="application/ld+json"]'):
        raw = script_node.text()
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, list):
            parsed.structured_data.extend(d for d in data if isinstance(d, dict))
        elif isinstance(data, dict):
            parsed.structured_data.append(data)

    footer_node = tree.css_first("footer")
    if footer_node:
        parsed.footer_text = _clean_text(footer_node.text())

    # Strip script/style before pulling visible text so JS/CSS don't leak in.
    for tag in tree.css("script, style, noscript"):
        tag.decompose()
    body_node = tree.css_first("body") or tree
    parsed.visible_text = _clean_text(body_node.text(separator=" "))

    seen_links: set[str] = set()
    for anchor in tree.css("a[href]"):
        href = anchor.attributes.get("href")
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute_url = urljoin(page_url, href).split("#")[0]
        if absolute_url in seen_links:
            continue
        seen_links.add(absolute_url)

        if absolute_url.lower().endswith(".pdf"):
            if is_same_registrable_domain(absolute_url, base_domain):
                parsed.pdf_links.append(absolute_url)
            continue

        if not is_same_registrable_domain(absolute_url, base_domain):
            continue
        if is_disallowed_path(absolute_url):
            continue

        parsed.internal_links.append(ParsedLink(url=absolute_url, anchor_text=_clean_text(anchor.text())))

    return parsed
