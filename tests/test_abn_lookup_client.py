from __future__ import annotations

from datetime import datetime, timedelta

import httpx
import pytest
import respx

from sil_research.extraction.abn_lookup import (
    AbnLookupError,
    AbnLookupResult,
    AbrJsonAbnLookupClient,
    CachingAbnLookupClient,
    MockAbnLookupClient,
    _parse_abn_details,
    _strip_jsonp,
)


def test_strip_jsonp_parses_plain_json():
    assert _strip_jsonp('{"Abn": "51824753556"}') == {"Abn": "51824753556"}


def test_strip_jsonp_parses_callback_wrapped_json():
    assert _strip_jsonp('callback({"Abn": "51824753556"});') == {"Abn": "51824753556"}


def test_strip_jsonp_raises_on_unrecognised_format():
    with pytest.raises(AbnLookupError):
        _strip_jsonp("<html>not json</html>")


def test_parse_abn_details_found_entity():
    payload = {
        "Abn": "51824753556",
        "AbnStatus": "Active",
        "EntityName": "Sunrise Living Pty Ltd",
        "EntityTypeCode": "PRV",
        "EntityTypeName": "Australian Private Company",
        "BusinessName": ["Sunrise Living"],
    }
    result = _parse_abn_details("51824753556", payload)
    assert result.found is True
    assert result.entity_name == "Sunrise Living Pty Ltd"
    assert result.business_names == ["Sunrise Living"]
    assert result.is_suppressed is False
    assert result.raw_response == payload


def test_parse_abn_details_not_found():
    payload = {"Abn": "00000000000", "Message": "ABN not found or invalid"}
    result = _parse_abn_details("00000000000", payload)
    assert result.found is False


def test_parse_abn_details_suppressed_entity():
    payload = {"Abn": "51824753556", "AbnStatus": "Active", "Message": "Information is suppressed"}
    result = _parse_abn_details("51824753556", payload)
    assert result.is_suppressed is True


def test_mock_abn_lookup_client_returns_configured_response_and_records_calls():
    client = MockAbnLookupClient(responses={"51824753556": AbnLookupResult(abn="51824753556", found=True, entity_name="Sunrise Living Pty Ltd")})
    result = client.lookup_abn("51824753556")
    assert result.found is True
    assert result.entity_name == "Sunrise Living Pty Ltd"
    assert client.abn_calls == ["51824753556"]


def test_mock_abn_lookup_client_defaults_to_not_found():
    client = MockAbnLookupClient()
    result = client.lookup_abn("12345678901")
    assert result.found is False


@respx.mock
def test_abr_json_client_calls_expected_endpoint_and_parses_response():
    respx.get("https://abr.business.gov.au/json/AbnDetails.aspx").mock(
        return_value=httpx.Response(200, text='{"Abn": "51824753556", "AbnStatus": "Active", "EntityName": "Sunrise Living Pty Ltd"}')
    )
    client = AbrJsonAbnLookupClient(guid="test-guid")
    result = client.lookup_abn("51824753556")
    assert result.found is True
    assert result.entity_name == "Sunrise Living Pty Ltd"


def test_abr_json_client_requires_a_guid():
    with pytest.raises(ValueError):
        AbrJsonAbnLookupClient(guid="")


def test_caching_client_stores_and_reuses_result(db_session, monkeypatch):
    import sil_research.database as database_module

    monkeypatch.setattr(database_module, "get_session_factory", lambda *_a, **_k: (lambda: db_session))
    monkeypatch.setattr(database_module.Session, "close", lambda self: None)

    inner = MockAbnLookupClient(responses={"51824753556": AbnLookupResult(abn="51824753556", found=True, entity_name="Sunrise Living Pty Ltd")})
    client = CachingAbnLookupClient(inner)

    first = client.lookup_abn("51824753556")
    second = client.lookup_abn("51824753556")

    assert first.entity_name == second.entity_name == "Sunrise Living Pty Ltd"
    assert inner.abn_calls == ["51824753556"], "second lookup should be served from cache, not the inner client"


def test_caching_client_refetches_after_ttl_expiry(db_session, monkeypatch):
    import sil_research.database as database_module

    monkeypatch.setattr(database_module, "get_session_factory", lambda *_a, **_k: (lambda: db_session))
    monkeypatch.setattr(database_module.Session, "close", lambda self: None)

    inner = MockAbnLookupClient(responses={"51824753556": AbnLookupResult(abn="51824753556", found=True, entity_name="Sunrise Living Pty Ltd")})
    client = CachingAbnLookupClient(inner, ttl=timedelta(seconds=-1))  # already expired

    client.lookup_abn("51824753556")
    client.lookup_abn("51824753556")

    assert inner.abn_calls == ["51824753556", "51824753556"]
