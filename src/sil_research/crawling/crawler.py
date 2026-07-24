"""The respectful, same-domain website crawler.

Concurrency happens across domains (via `crawl_many`, backed by a thread
pool bounded by `concurrency`); within a single domain, requests are always
sequential and paced by `delay_seconds` so no domain ever sees concurrent
or bursty traffic from this tool.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from sil_research.crawling.link_prioritiser import normalise_url, order_links_by_priority, seed_urls
from sil_research.crawling.page_parser import ParsedPage, is_disallowed_path, parse_page
from sil_research.crawling.pdf_parser import ParsedPdf, extract_pdf_text
from sil_research.crawling.robots import RobotsChecker
from sil_research.discovery.domain_filter import canonical_domain

logger = logging.getLogger(__name__)

_MAX_DOCUMENTS_PER_DOMAIN = 10


@dataclass
class CrawledPage:
    url: str
    canonical_url: str
    http_status: int | None
    fetched_at: datetime
    parsed: ParsedPage | None = None
    error: str | None = None


@dataclass
class CrawledDocument:
    source_url: str
    fetched_at: datetime
    parsed: ParsedPdf | None = None
    error: str | None = None


@dataclass
class CrawlOutcome:
    domain: str
    pages: list[CrawledPage] = field(default_factory=list)
    documents: list[CrawledDocument] = field(default_factory=list)
    robots_disallowed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class Crawler:
    def __init__(
        self,
        user_agent: str,
        delay_seconds: float = 2.0,
        max_pages_per_domain: int = 25,
        timeout_seconds: float = 15.0,
        max_retries: int = 2,
        client: httpx.Client | None = None,
        robots_checker: RobotsChecker | None = None,
        sleep_fn=time.sleep,
    ) -> None:
        self.user_agent = user_agent
        self.delay_seconds = delay_seconds
        self.max_pages_per_domain = max_pages_per_domain
        self.max_retries = max_retries
        self._client = client or httpx.Client(
            timeout=timeout_seconds, follow_redirects=True, headers={"User-Agent": user_agent}
        )
        self._robots = robots_checker or RobotsChecker(timeout=timeout_seconds)
        self._sleep = sleep_fn

    def _fetch(self, url: str) -> httpx.Response:
        @retry(
            reraise=True,
            stop=stop_after_attempt(self.max_retries + 1),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            retry=retry_if_exception_type(httpx.TransportError),
        )
        def _do_fetch() -> httpx.Response:
            return self._client.get(url)

        return _do_fetch()

    def crawl(self, website_url: str) -> CrawlOutcome:
        domain = canonical_domain(website_url)
        outcome = CrawlOutcome(domain=domain)

        visited: set[str] = set()
        frontier: deque[str] = deque(u for u in seed_urls(website_url) if not is_disallowed_path(u))
        seen_frontier: set[str] = {normalise_url(u) for u in frontier}
        documents_fetched = 0

        first_request = True
        while frontier and len(visited) < self.max_pages_per_domain:
            url = frontier.popleft()
            canonical = normalise_url(url)
            if canonical in visited:
                continue
            visited.add(canonical)

            robots_result = self._robots.can_fetch(url, self.user_agent)
            if robots_result.fetch_error:
                outcome.errors.append(f"{url}: {robots_result.fetch_error}")
            if not robots_result.allowed:
                outcome.robots_disallowed.append(url)
                continue

            if not first_request:
                self._sleep(self.delay_seconds)
            first_request = False

            try:
                response = self._fetch(url)
            except httpx.HTTPError as exc:
                outcome.pages.append(
                    CrawledPage(
                        url=url,
                        canonical_url=canonical,
                        http_status=None,
                        fetched_at=datetime.utcnow(),
                        error=str(exc),
                    )
                )
                outcome.errors.append(f"{url}: {exc}")
                continue

            fetched_at = datetime.utcnow()
            if response.status_code in (401, 403):
                outcome.pages.append(
                    CrawledPage(
                        url=url,
                        canonical_url=canonical,
                        http_status=response.status_code,
                        fetched_at=fetched_at,
                        error="Access restricted (login-protected or blocked); not retrying",
                    )
                )
                continue

            if response.status_code != 200:
                outcome.pages.append(
                    CrawledPage(
                        url=url,
                        canonical_url=canonical,
                        http_status=response.status_code,
                        fetched_at=fetched_at,
                        error=f"Non-200 status: {response.status_code}",
                    )
                )
                continue

            content_type = response.headers.get("content-type", "")
            if "text/html" not in content_type:
                outcome.pages.append(
                    CrawledPage(
                        url=url, canonical_url=canonical, http_status=200, fetched_at=fetched_at
                    )
                )
                continue

            parsed = parse_page(response.text, page_url=url, base_domain=domain)
            outcome.pages.append(
                CrawledPage(url=url, canonical_url=canonical, http_status=200, fetched_at=fetched_at, parsed=parsed)
            )

            # Links actually found on the site take priority over any
            # remaining, still-untested seed-path guesses: they're inserted
            # ahead of the existing frontier rather than appended, so a
            # real site's own navigation drives the crawl instead of being
            # crowded out by a long list of slug guesses that 404.
            remaining_capacity = self.max_pages_per_domain - len(visited)
            if remaining_capacity > 0:
                new_links = [
                    link
                    for link in order_links_by_priority(parsed.internal_links)
                    if normalise_url(link.url) not in visited and normalise_url(link.url) not in seen_frontier
                ][:remaining_capacity]
                for link in new_links:
                    seen_frontier.add(normalise_url(link.url))
                frontier.extendleft(reversed([link.url for link in new_links]))

            for pdf_url in parsed.pdf_links:
                if documents_fetched >= _MAX_DOCUMENTS_PER_DOMAIN:
                    break
                self._sleep(self.delay_seconds)
                doc = self._fetch_document(pdf_url)
                outcome.documents.append(doc)
                documents_fetched += 1

        return outcome

    def _fetch_document(self, url: str) -> CrawledDocument:
        robots_result = self._robots.can_fetch(url, self.user_agent)
        if not robots_result.allowed:
            return CrawledDocument(source_url=url, fetched_at=datetime.utcnow(), error="Disallowed by robots.txt")
        try:
            response = self._fetch(url)
        except httpx.HTTPError as exc:
            return CrawledDocument(source_url=url, fetched_at=datetime.utcnow(), error=str(exc))

        fetched_at = datetime.utcnow()
        if response.status_code != 200:
            return CrawledDocument(
                source_url=url, fetched_at=fetched_at, error=f"Non-200 status: {response.status_code}"
            )

        parsed = extract_pdf_text(response.content, source_url=url)
        return CrawledDocument(source_url=url, fetched_at=fetched_at, parsed=parsed)

    def crawl_many(self, website_urls: list[str], concurrency: int = 4) -> dict[str, CrawlOutcome]:
        results: dict[str, CrawlOutcome] = {}
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as executor:
            future_to_url = {executor.submit(self.crawl, url): url for url in website_urls}
            for future in as_completed(future_to_url):
                url = future_to_url[future]
                try:
                    results[url] = future.result()
                except Exception as exc:  # keep one provider's failure from stopping the batch
                    logger.exception("Crawl failed for %s", url)
                    results[url] = CrawlOutcome(domain=canonical_domain(url), errors=[str(exc)])
        return results
