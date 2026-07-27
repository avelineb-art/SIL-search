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


class SerpApiProvider(SearchProvider):
    """SerpApi (https://serpapi.com) - proxies Google Search results without
    requiring a Programmable Search Engine.

    Fixed to an Australian-biased Google search (google_domain=google.com.au,
    gl=au country targeting) regardless of query text, since query text alone
    (the location token appended by the query generator) doesn't influence
    which country's Google index/ranking is used - `gl`/`google_domain` do.
    State/city targeting itself still comes from the query generator's
    location-priority list (NSW/VIC/QLD first by default;
    see config/locations.yml) appending a location token to each query.

    Note: SerpApi's free tier is 100 searches **per month**, not per day
    like Google CSE's free tier - set SIL_DAILY_QUERY_BUDGET accordingly if
    you're on the free plan.
    """

    ENDPOINT = "https://serpapi.com/search"

    def __init__(
        self,
        api_key: str,
        google_domain: str = "google.com.au",
        country: str = "au",
        language: str = "en",
        location: str | None = "Australia",
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("SerpApiProvider requires an api_key - register at https://serpapi.com")
        self._api_key = api_key
        self._google_domain = google_domain
        self._country = country
        self._language = language
        self._location = location
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
        params = {
            "engine": "google",
            "api_key": self._api_key,
            "q": query,
            "google_domain": self._google_domain,
            "gl": self._country,
            "hl": self._language,
            "start": start,
            "num": min(limit, 100),
        }
        if self._location:
            params["location"] = self._location

        try:
            response = self._get(params)
        except httpx.TransportError as exc:
            raise SearchProviderError(f"Network error calling SerpApi: {exc}") from exc

        if response.status_code == 429:
            raise SearchQuotaExceededError("SerpApi returned HTTP 429 (rate limited)")

        try:
            payload = response.json()
        except ValueError:
            payload = {}

        error_message = str(payload.get("error", "")) if isinstance(payload, dict) else ""
        if error_message:
            lowered = error_message.lower()
            if any(keyword in lowered for keyword in ("run out", "quota", "exceeded", "plan limit")):
                raise SearchQuotaExceededError(f"SerpApi quota exceeded: {error_message}")
            raise SearchProviderError(f"SerpApi returned an error: {error_message}")

        if response.status_code != 200:
            raise SearchProviderError(f"SerpApi returned HTTP {response.status_code}: {response.text[:500]}")

        organic_results = payload.get("organic_results", [])
        results = []
        for idx, item in enumerate(organic_results):
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


class BraveSearchProvider(SearchProvider):
    """Brave Search API (https://brave.com/search/api/).

    Authenticates via the `X-Subscription-Token` header (never as a query
    param, so it never ends up in logs/URLs). Defaults to `country="AU"` to
    bias results to Australia - state/city targeting itself still comes
    from the query generator's location list (NSW/VIC/QLD first by
    default; see config/locations.yml).

    Note: Brave's API paginates by *page* via `offset` (0-9), not by raw
    result index like Google/SerpApi - `offset` here is derived as
    `start // count`, which only lines up cleanly if callers always request
    the same `limit` for a given query (true of every call in this
    codebase today, since nothing paginates within a single `discover` run
    yet).
    """

    ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str, country: str = "AU", search_lang: str = "en", client: httpx.Client | None = None) -> None:
        if not api_key:
            raise ValueError("BraveSearchProvider requires an api_key - register at https://brave.com/search/api/")
        self._api_key = api_key
        self._country = country
        self._search_lang = search_lang
        self._client = client or httpx.Client(timeout=15.0)

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(httpx.TransportError),
    )
    def _get(self, params: dict) -> httpx.Response:
        return self._client.get(
            self.ENDPOINT,
            params=params,
            headers={"Accept": "application/json", "X-Subscription-Token": self._api_key},
        )

    def search(self, query: str, start: int = 0, limit: int = 10) -> list[SearchResult]:
        count = max(1, min(limit, 20))
        offset = start // count

        params = {
            "q": query,
            "country": self._country,
            "search_lang": self._search_lang,
            "count": count,
            "offset": offset,
        }
        try:
            response = self._get(params)
        except httpx.TransportError as exc:
            raise SearchProviderError(f"Network error calling Brave Search API: {exc}") from exc

        if response.status_code == 429:
            raise SearchQuotaExceededError("Brave Search API returned HTTP 429 (rate limited)")
        if response.status_code in (401, 403):
            raise SearchProviderError(
                f"Brave Search API auth error (HTTP {response.status_code}): {response.text[:500]}"
            )
        if response.status_code != 200:
            raise SearchProviderError(f"Brave Search API returned HTTP {response.status_code}: {response.text[:500]}")

        payload = response.json()
        items = (payload.get("web") or {}).get("results") or payload.get("results") or []
        results = []
        for idx, item in enumerate(items):
            results.append(
                SearchResult(
                    url=item.get("url", ""),
                    title=item.get("title", ""),
                    snippet=item.get("description", ""),
                    rank=start + idx,
                    query=query,
                )
            )
        return results
