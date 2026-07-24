"""ABN Lookup web-services client.

Confirms an extracted ABN is active and resolves the legal entity name/type
from the official ABN Lookup service, so the legal name used for register
matching comes from ABN Lookup rather than from fuzzy string matching
wherever an ABN can be confirmed (per the build spec).

Implementation note: the ABN Lookup JSON endpoints
(`abr.business.gov.au/json/AbnDetails.aspx`, `.../MatchingNames.aspx`) are
publicly documented but their exact response field casing could not be
confirmed from this environment (the ABR docs pages return HTTP 403 to
automated fetching). The field names below (`Abn`, `AbnStatus`,
`EntityName`, `EntityTypeCode`, `EntityTypeName`, `BusinessName`, `Message`)
match the commonly published shape of this API; the parser looks them up
case-insensitively and always retains the full raw response, so a
real-world mismatch is recoverable from the cache without re-querying the
API, and should be verified against a live GUID before this client is
depended on for anything automated.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx


class AbnLookupError(Exception):
    """Raised for genuine transport/parsing failures, not for a "not found" result."""


@dataclass
class AbnLookupResult:
    abn: str
    found: bool
    abn_status: str | None = None
    entity_name: str | None = None
    entity_type_code: str | None = None
    entity_type_name: str | None = None
    business_names: list[str] = field(default_factory=list)
    is_suppressed: bool = False
    message: str | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AbnNameMatch:
    abn: str
    name: str
    score: float | None = None


class AbnLookupClient(ABC):
    @abstractmethod
    def lookup_abn(self, abn: str) -> AbnLookupResult:
        raise NotImplementedError

    @abstractmethod
    def search_by_name(self, name: str) -> list[AbnNameMatch]:
        raise NotImplementedError


def _get_ci(payload: dict, *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    lowered = {str(k).lower(): v for k, v in payload.items()}
    for key in keys:
        if key.lower() in lowered:
            return lowered[key.lower()]
    return None


def _strip_jsonp(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.match(r"^[^(]*\((.*)\)\s*;?\s*$", text, re.DOTALL)
    if not match:
        raise AbnLookupError(f"Unrecognised ABN Lookup response format: {text[:200]!r}")
    return json.loads(match.group(1))


def _parse_abn_details(abn: str, payload: dict) -> AbnLookupResult:
    message = _get_ci(payload, "Message", "message")
    abn_status = _get_ci(payload, "AbnStatus", "abnStatus")
    entity_name = _get_ci(payload, "EntityName", "entityName")
    entity_type_code = _get_ci(payload, "EntityTypeCode", "entityTypeCode")
    entity_type_name = _get_ci(payload, "EntityTypeName", "entityTypeName")

    business_name_raw = _get_ci(payload, "BusinessName", "businessName") or []
    if isinstance(business_name_raw, str):
        business_names = [business_name_raw] if business_name_raw else []
    elif isinstance(business_name_raw, list):
        business_names = [b for b in business_name_raw if b]
    else:
        business_names = []

    message_text = str(message).lower() if message else ""
    not_found = "not found" in message_text or "invalid abn" in message_text
    is_suppressed = "suppress" in message_text

    found = bool(entity_name or abn_status) and not not_found

    return AbnLookupResult(
        abn=abn,
        found=found,
        abn_status=abn_status,
        entity_name=entity_name,
        entity_type_code=entity_type_code,
        entity_type_name=entity_type_name,
        business_names=business_names,
        is_suppressed=is_suppressed,
        message=message,
        raw_response=payload,
    )


def _parse_name_matches(payload: dict) -> list[AbnNameMatch]:
    names = _get_ci(payload, "Names", "names") or []
    matches = []
    for item in names:
        if not isinstance(item, dict):
            continue
        abn = _get_ci(item, "Abn", "abn")
        name = _get_ci(item, "Name", "name", "OrganisationName")
        score = _get_ci(item, "Score", "score")
        if abn and name:
            matches.append(AbnNameMatch(abn=str(abn), name=str(name), score=float(score) if score is not None else None))
    return matches


class AbrJsonAbnLookupClient(AbnLookupClient):
    """Live client against the ABN Lookup JSON web services."""

    ABN_DETAILS_URL = "https://abr.business.gov.au/json/AbnDetails.aspx"
    NAME_SEARCH_URL = "https://abr.business.gov.au/json/MatchingNames.aspx"

    def __init__(self, guid: str, client: httpx.Client | None = None) -> None:
        if not guid:
            raise ValueError(
                "AbrJsonAbnLookupClient requires a GUID - register for free at "
                "https://abr.business.gov.au/Tools/WebServices and set ABN_LOOKUP_GUID."
            )
        self._guid = guid
        self._client = client or httpx.Client(timeout=15.0)

    def lookup_abn(self, abn: str) -> AbnLookupResult:
        try:
            response = self._client.get(self.ABN_DETAILS_URL, params={"abn": abn, "guid": self._guid, "callback": ""})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AbnLookupError(f"ABN Lookup request failed for {abn}: {exc}") from exc
        return _parse_abn_details(abn, _strip_jsonp(response.text))

    def search_by_name(self, name: str) -> list[AbnNameMatch]:
        try:
            response = self._client.get(self.NAME_SEARCH_URL, params={"name": name, "guid": self._guid, "callback": ""})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AbnLookupError(f"ABN Lookup name search failed for {name!r}: {exc}") from exc
        return _parse_name_matches(_strip_jsonp(response.text))


class MockAbnLookupClient(AbnLookupClient):
    """Offline client for tests and dry runs."""

    def __init__(
        self,
        responses: dict[str, AbnLookupResult] | None = None,
        name_matches: dict[str, list[AbnNameMatch]] | None = None,
    ) -> None:
        self._responses = responses or {}
        self._name_matches = name_matches or {}
        self.abn_calls: list[str] = []
        self.name_calls: list[str] = []

    def lookup_abn(self, abn: str) -> AbnLookupResult:
        self.abn_calls.append(abn)
        return self._responses.get(abn, AbnLookupResult(abn=abn, found=False, message="Not found"))

    def search_by_name(self, name: str) -> list[AbnNameMatch]:
        self.name_calls.append(name)
        return self._name_matches.get(name, [])


def _result_to_cache_payload(result: AbnLookupResult) -> dict:
    return asdict(result)


def _result_from_cache_payload(payload: dict) -> AbnLookupResult:
    return AbnLookupResult(
        abn=payload.get("abn", ""),
        found=payload.get("found", False),
        abn_status=payload.get("abn_status"),
        entity_name=payload.get("entity_name"),
        entity_type_code=payload.get("entity_type_code"),
        entity_type_name=payload.get("entity_type_name"),
        business_names=payload.get("business_names", []),
        is_suppressed=payload.get("is_suppressed", False),
        message=payload.get("message"),
        raw_response=payload.get("raw_response", {}),
    )


class CachingAbnLookupClient(AbnLookupClient):
    """Wraps any AbnLookupClient with DB-backed caching of ABN lookups, to
    respect the service and avoid redundant calls across runs. Name
    searches are not cached (a free-text query isn't a stable cache key).
    """

    def __init__(self, inner: AbnLookupClient, ttl: timedelta = timedelta(days=30)) -> None:
        self._inner = inner
        self._ttl = ttl

    def lookup_abn(self, abn: str) -> AbnLookupResult:
        from sil_research.database import AbnLookupCache, session_scope

        with session_scope() as session:
            cached = session.get(AbnLookupCache, abn)
            if cached and cached.ttl_expires_at and cached.ttl_expires_at > datetime.utcnow():
                return _result_from_cache_payload(cached.response_json)

        result = self._inner.lookup_abn(abn)

        with session_scope() as session:
            payload = _result_to_cache_payload(result)
            existing = session.get(AbnLookupCache, abn)
            if existing:
                existing.response_json = payload
                existing.fetched_at = datetime.utcnow()
                existing.ttl_expires_at = datetime.utcnow() + self._ttl
            else:
                session.add(
                    AbnLookupCache(
                        abn=abn,
                        response_json=payload,
                        fetched_at=datetime.utcnow(),
                        ttl_expires_at=datetime.utcnow() + self._ttl,
                    )
                )
        return result

    def search_by_name(self, name: str) -> list[AbnNameMatch]:
        return self._inner.search_by_name(name)
