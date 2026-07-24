from __future__ import annotations

from sil_research.crawling.link_prioritiser import (
    link_priority_score,
    normalise_url,
    order_links_by_priority,
    seed_urls,
)
from sil_research.crawling.page_parser import ParsedLink


def test_normalise_url_strips_trailing_slash():
    assert normalise_url("https://example.com.au/services/") == normalise_url("https://example.com.au/services")


def test_normalise_url_lowercases_scheme_and_host():
    assert normalise_url("HTTPS://Example.COM.au/Services") == normalise_url("https://example.com.au/Services")


def test_normalise_url_strips_tracking_params():
    a = normalise_url("https://example.com.au/services?utm_source=facebook&utm_medium=cpc")
    b = normalise_url("https://example.com.au/services")
    assert a == b


def test_normalise_url_keeps_meaningful_query_params():
    a = normalise_url("https://example.com.au/search?region=vic")
    b = normalise_url("https://example.com.au/search")
    assert a != b


def test_normalise_url_ignores_fragment():
    a = normalise_url("https://example.com.au/services#team")
    b = normalise_url("https://example.com.au/services")
    assert a == b


def test_normalise_url_sorts_query_params_for_stable_dedup():
    a = normalise_url("https://example.com.au/search?b=2&a=1")
    b = normalise_url("https://example.com.au/search?a=1&b=2")
    assert a == b


def test_seed_urls_builds_absolute_priority_paths():
    urls = seed_urls("https://example.com.au")
    assert "https://example.com.au/" in urls
    assert any(u.endswith("/supported-independent-living") for u in urls)


def test_link_priority_score_favours_keyword_matches():
    high = ParsedLink(url="https://example.com.au/supported-independent-living", anchor_text="SIL vacancies")
    low = ParsedLink(url="https://example.com.au/terms-and-conditions", anchor_text="Terms")
    assert link_priority_score(high) > link_priority_score(low)


def test_order_links_by_priority_sorts_descending():
    links = [
        ParsedLink(url="https://example.com.au/terms", anchor_text="Terms"),
        ParsedLink(url="https://example.com.au/sil-vacancies", anchor_text="SIL vacancies"),
    ]
    ordered = order_links_by_priority(links)
    assert ordered[0].url.endswith("sil-vacancies")
