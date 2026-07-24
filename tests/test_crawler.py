from __future__ import annotations

import httpx
import respx

from sil_research.crawling.crawler import Crawler

BASE = "https://fakeprovider.com.au"

INDEX_HTML = """
<html><head><title>Home</title></head><body>
<h1>Fake Provider</h1>
<p>We provide Supported Independent Living to NDIS participants.</p>
<a href="/services">Services</a>
<a href="/services/">Services trailing slash</a>
<a href="/blocked-by-robots">Blocked</a>
</body></html>
"""

SERVICES_HTML = """
<html><head><title>Services</title></head><body>
<p>Supported Independent Living with 24/7 support.</p>
</body></html>
"""

ROBOTS_TXT = "User-agent: *\nDisallow: /blocked-by-robots\n"


@respx.mock
def test_crawler_respects_robots_and_dedupes_url_variants():
    respx.get(f"{BASE}/robots.txt").mock(return_value=httpx.Response(200, text=ROBOTS_TXT))
    respx.get(f"{BASE}/").mock(return_value=httpx.Response(200, text=INDEX_HTML, headers={"content-type": "text/html"}))
    respx.get(f"{BASE}/services").mock(return_value=httpx.Response(200, text=SERVICES_HTML, headers={"content-type": "text/html"}))
    respx.get(f"{BASE}/services/").mock(return_value=httpx.Response(200, text=SERVICES_HTML, headers={"content-type": "text/html"}))
    respx.route(method="GET", url__regex=rf"{BASE}/.*").mock(return_value=httpx.Response(404, text="not found"))

    crawler = Crawler(
        user_agent="TestBot/1.0",
        delay_seconds=0,
        max_pages_per_domain=5,
        timeout_seconds=5,
        sleep_fn=lambda _seconds: None,
    )
    outcome = crawler.crawl(f"{BASE}/")

    assert f"{BASE}/blocked-by-robots" in outcome.robots_disallowed
    assert not any(p.canonical_url.endswith("blocked-by-robots") for p in outcome.pages)

    services_pages = [p for p in outcome.pages if p.canonical_url == f"{BASE}/services"]
    assert len(services_pages) == 1, "the /services and /services/ variants must dedupe to one crawl"

    homepage_pages = [p for p in outcome.pages if p.canonical_url == f"{BASE}/"]
    assert len(homepage_pages) == 1
    assert homepage_pages[0].parsed is not None
    assert "Supported Independent Living" in homepage_pages[0].parsed.visible_text

    assert len(outcome.pages) <= 5


@respx.mock
def test_crawl_many_handles_multiple_domains_independently():
    for host in ["providera.com.au", "providerb.com.au"]:
        respx.get(f"https://{host}/robots.txt").mock(return_value=httpx.Response(404))
        respx.get(f"https://{host}/").mock(
            return_value=httpx.Response(200, text="<html><body>Hello</body></html>", headers={"content-type": "text/html"})
        )
        respx.route(method="GET", url__regex=rf"https://{host}/.*").mock(return_value=httpx.Response(404))

    crawler = Crawler(
        user_agent="TestBot/1.0",
        delay_seconds=0,
        max_pages_per_domain=2,
        timeout_seconds=5,
        sleep_fn=lambda _seconds: None,
    )
    outcomes = crawler.crawl_many(["https://providera.com.au/", "https://providerb.com.au/"], concurrency=2)

    assert set(outcomes.keys()) == {"https://providera.com.au/", "https://providerb.com.au/"}
    for outcome in outcomes.values():
        assert any(p.canonical_url.endswith("/") for p in outcome.pages)
