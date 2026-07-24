"""Concrete SearchProvider implementations."""

from __future__ import annotations

import logging

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from sil_research.discovery.base import (
    SearchProvider,
    SearchProviderError,
    SearchQuotaExceededError,
    SearchResult,
)

logger = logging.getLogger(__name__)


class MockSearchProvider(SearchProvider):
    """Deterministic, offline SearchProvider for tests and dry runs.

    Accepts a mapping of query -> list of (url, title, snippet) tuples, or
    falls back to an empty result set for unknown queries. This lets the
    rest of the pipeline (query generation, dedup, crawling, classification)
    be built and tested end-to-end before any real API credentials exist.
    """

    def __init__(self, canned_results: dict[str, list[tuple[str, str, str]]] | None = None) -> None:
        self._canned_results = canned_results or {}
        self.calls: list[str] = []

    def search(self, query: str, start: int = 0, limit: int = 10) -> list[SearchResult]:
        self.calls.append(query)
        rows = self._canned_results.get(query, [])
        page = rows[start : start + limit]
        return [
            SearchResult(url=url, title=title, snippet=snippet, rank=start + idx, query=query)
            for idx, (url, title, snippet) in enumerate(page)
        ]


class GoogleCSEProvider(SearchProvider):
    """Google Programmable Search (Custom Search JSON API).

    Requires an API key and a Programmable Search Engine ID (cx), supplied
    via environment variables - never hard-coded. See
    https://developers.google.com/custom-search/v1/overview
    """

    ENDPOINT = "https://www.googleapis.com/customsearch/v1"

    def __init__(self, api_key: str, cx: str, client: httpx.Client | None = None) -> None:
        if not api_key or not cx:
            raise ValueError("GoogleCSEProvider requires both an api_key and a cx (search engine id)")
        self._api_key = api_key
        self._cx = cx
        self._client = client or httpx.Client(timeout=15.0)

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(httpx.TransportError),
    )
    def _get(self, params: dict) -> httpx.Response:
        return self._client.get(self.ENDPOINT, params=params)

    def search(self, query: str, start: int = 0, limit: int = 10) -> list[SearchResult]:
        # Google CSE's `start` parameter is 1-indexed and caps at 100 results total.
        params = {
            "key": self._api_key,
            "cx": self._cx,
            "q": query,
            "start": start + 1,
            "num": min(limit, 10),
        }
        try:
            response = self._get(params)
        except httpx.TransportError as exc:
            raise SearchProviderError(f"Network error calling Google CSE: {exc}") from exc

        if response.status_code == 429:
            raise SearchQuotaExceededError("Google CSE returned HTTP 429 (rate limited)")
        if response.status_code == 403:
            body = response.text.lower()
            if "quota" in body or "daily limit" in body:
                raise SearchQuotaExceededError("Google CSE daily quota exceeded")
            raise SearchProviderError(f"Google CSE returned HTTP 403: {response.text[:500]}")
        if response.status_code != 200:
            raise SearchProviderError(
                f"Google CSE returned HTTP {response.status_code}: {response.text[:500]}"
            )

        payload = response.json()
        items = payload.get("items", [])
        results = []
        for idx, item in enumerate(items):
            results.append(
                SearchResult(
                    url=item.get("link", ""),
                    title=item.get("title", ""),
                    snippet=item.get("snippet", ""),
                    rank=start + idx,
                    query=query,
                )
            )
        return results
