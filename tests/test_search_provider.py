from __future__ import annotations

import httpx
import pytest
import respx

from sil_research.discovery.base import SearchProviderError, SearchQuotaExceededError
from sil_research.discovery.search_provider import (
    BraveSearchProvider,
    GoogleCSEProvider,
    MockSearchProvider,
    SerpApiProvider,
)


def test_mock_search_provider_returns_canned_results_and_records_calls():
    provider = MockSearchProvider({"sil nsw": [("https://a.com.au/", "A", "snippet a")]})
    results = provider.search("sil nsw")
    assert len(results) == 1
    assert results[0].url == "https://a.com.au/"
    assert provider.calls == ["sil nsw"]


def test_mock_search_provider_unknown_query_returns_empty():
    provider = MockSearchProvider()
    assert provider.search("anything") == []


@respx.mock
def test_google_cse_provider_parses_items():
    respx.get("https://www.googleapis.com/customsearch/v1").mock(
        return_value=httpx.Response(200, json={"items": [{"link": "https://a.com.au/", "title": "A", "snippet": "s"}]})
    )
    provider = GoogleCSEProvider(api_key="key", cx="cx")
    results = provider.search("sil nsw")
    assert results[0].url == "https://a.com.au/"


@respx.mock
def test_google_cse_provider_raises_quota_exceeded_on_429():
    respx.get("https://www.googleapis.com/customsearch/v1").mock(return_value=httpx.Response(429))
    provider = GoogleCSEProvider(api_key="key", cx="cx")
    with pytest.raises(SearchQuotaExceededError):
        provider.search("sil nsw")


def test_serpapi_provider_requires_api_key():
    with pytest.raises(ValueError):
        SerpApiProvider(api_key="")


@respx.mock
def test_serpapi_provider_parses_organic_results():
    route = respx.get("https://serpapi.com/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "organic_results": [
                    {"link": "https://sunriseliving.com.au/", "title": "Sunrise Living", "snippet": "SIL provider in NSW"},
                    {"link": "https://beaconcare.com.au/", "title": "Beacon Care", "snippet": "Supported independent living"},
                ]
            },
        )
    )
    provider = SerpApiProvider(api_key="test-key")
    results = provider.search('"supported independent living" NDIS New South Wales', limit=10)

    assert len(results) == 2
    assert results[0].url == "https://sunriseliving.com.au/"
    assert results[1].title == "Beacon Care"

    request = route.calls.last.request
    params = dict(httpx.QueryParams(request.url.query))
    assert params["engine"] == "google"
    assert params["api_key"] == "test-key"
    assert params["google_domain"] == "google.com.au"
    assert params["gl"] == "au"
    assert params["hl"] == "en"
    assert params["location"] == "Australia"
    assert params["q"] == '"supported independent living" NDIS New South Wales'


@respx.mock
def test_serpapi_provider_respects_custom_locale_settings():
    respx.get("https://serpapi.com/search").mock(return_value=httpx.Response(200, json={"organic_results": []}))
    provider = SerpApiProvider(api_key="test-key", google_domain="google.com", country="us", language="en", location=None)
    provider.search("test query")

    request = respx.calls.last.request
    params = dict(httpx.QueryParams(request.url.query))
    assert params["google_domain"] == "google.com"
    assert params["gl"] == "us"
    assert "location" not in params


@respx.mock
def test_serpapi_provider_raises_quota_exceeded_on_429():
    respx.get("https://serpapi.com/search").mock(return_value=httpx.Response(429))
    provider = SerpApiProvider(api_key="test-key")
    with pytest.raises(SearchQuotaExceededError):
        provider.search("sil nsw")


@respx.mock
def test_serpapi_provider_raises_quota_exceeded_on_run_out_of_searches_message():
    respx.get("https://serpapi.com/search").mock(
        return_value=httpx.Response(200, json={"error": "You have run out of searches for this month."})
    )
    provider = SerpApiProvider(api_key="test-key")
    with pytest.raises(SearchQuotaExceededError):
        provider.search("sil nsw")


@respx.mock
def test_serpapi_provider_raises_generic_error_for_other_error_messages():
    respx.get("https://serpapi.com/search").mock(return_value=httpx.Response(200, json={"error": "Invalid API key."}))
    provider = SerpApiProvider(api_key="bad-key")
    with pytest.raises(SearchProviderError):
        provider.search("sil nsw")


@respx.mock
def test_serpapi_provider_pagination_uses_start_offset():
    route = respx.get("https://serpapi.com/search").mock(return_value=httpx.Response(200, json={"organic_results": []}))
    provider = SerpApiProvider(api_key="test-key")
    provider.search("sil nsw", start=20, limit=10)

    request = route.calls.last.request
    params = dict(httpx.QueryParams(request.url.query))
    assert params["start"] == "20"
    assert params["num"] == "10"


def test_brave_search_provider_requires_api_key():
    with pytest.raises(ValueError):
        BraveSearchProvider(api_key="")


@respx.mock
def test_brave_search_provider_parses_web_results_and_sends_token_header():
    route = respx.get("https://api.search.brave.com/res/v1/web/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {"title": "Sunrise Living", "url": "https://sunriseliving.com.au/", "description": "SIL provider in NSW"},
                        {"title": "Beacon Care", "url": "https://beaconcare.com.au/", "description": "Supported independent living"},
                    ]
                }
            },
        )
    )
    provider = BraveSearchProvider(api_key="test-token")
    results = provider.search('"supported independent living" NDIS New South Wales', limit=10)

    assert len(results) == 2
    assert results[0].url == "https://sunriseliving.com.au/"
    assert results[0].snippet == "SIL provider in NSW"
    assert results[1].title == "Beacon Care"

    request = route.calls.last.request
    assert request.headers["X-Subscription-Token"] == "test-token"
    assert "test-token" not in str(request.url), "the API key must never be sent as a query param"
    params = dict(httpx.QueryParams(request.url.query))
    assert params["country"] == "AU"
    assert params["search_lang"] == "en"
    assert params["count"] == "10"


@respx.mock
def test_brave_search_provider_falls_back_to_top_level_results_key():
    respx.get("https://api.search.brave.com/res/v1/web/search").mock(
        return_value=httpx.Response(200, json={"results": [{"title": "A", "url": "https://a.com.au/", "description": "d"}]})
    )
    provider = BraveSearchProvider(api_key="test-token")
    results = provider.search("sil nsw")
    assert len(results) == 1
    assert results[0].url == "https://a.com.au/"


@respx.mock
def test_brave_search_provider_raises_quota_exceeded_on_429():
    respx.get("https://api.search.brave.com/res/v1/web/search").mock(return_value=httpx.Response(429))
    provider = BraveSearchProvider(api_key="test-token")
    with pytest.raises(SearchQuotaExceededError):
        provider.search("sil nsw")


@respx.mock
def test_brave_search_provider_raises_on_auth_error():
    respx.get("https://api.search.brave.com/res/v1/web/search").mock(return_value=httpx.Response(401, text="Invalid subscription token"))
    provider = BraveSearchProvider(api_key="bad-token")
    with pytest.raises(SearchProviderError):
        provider.search("sil nsw")


@respx.mock
def test_brave_search_provider_pagination_derives_offset_from_start_and_count():
    route = respx.get("https://api.search.brave.com/res/v1/web/search").mock(return_value=httpx.Response(200, json={"web": {"results": []}}))
    provider = BraveSearchProvider(api_key="test-token")
    provider.search("sil nsw", start=20, limit=10)

    request = route.calls.last.request
    params = dict(httpx.QueryParams(request.url.query))
    assert params["offset"] == "2"
    assert params["count"] == "10"


@respx.mock
def test_brave_search_provider_caps_count_at_twenty():
    route = respx.get("https://api.search.brave.com/res/v1/web/search").mock(return_value=httpx.Response(200, json={"web": {"results": []}}))
    provider = BraveSearchProvider(api_key="test-token")
    provider.search("sil nsw", limit=50)

    request = route.calls.last.request
    params = dict(httpx.QueryParams(request.url.query))
    assert params["count"] == "20"
