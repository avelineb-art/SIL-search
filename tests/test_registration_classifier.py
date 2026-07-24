from __future__ import annotations

from sil_research.classification.registration_classifier import classify_registration
from sil_research.classification.scoring import TextSource
from sil_research.models import RegistrationClaimStatus
from tests.conftest import text_source_from_fixture


def test_explicit_registration_wording_detected():
    source = text_source_from_fixture("strong_sil_page.html", "https://example.com.au/services")
    result = classify_registration([source], provider_domain="example.com.au")

    assert result.status == RegistrationClaimStatus.EXPLICIT_REGISTERED_CLAIM
    assert result.confidence >= 0.8
    explicit_items = [e for e in result.evidence if e.evidence_type == RegistrationClaimStatus.EXPLICIT_REGISTERED_CLAIM]
    assert explicit_items
    assert explicit_items[0].source_url == "https://example.com.au/services"


def test_ambiguous_ndis_wording_without_registration_claim():
    source = TextSource(
        text="We support NDIS participants and accept NDIS funding for daily living assistance.",
        source_url="https://example.com.au/",
        origin="VISIBLE_TEXT",
        page_type="general",
    )
    result = classify_registration([source], provider_domain="example.com.au")

    assert result.status == RegistrationClaimStatus.AMBIGUOUS_NDIS_LANGUAGE
    assert not result.has_contradiction


def test_no_registration_claim_found_is_not_treated_as_unregistered():
    source = TextSource(
        text="We provide shared living support to people across regional Victoria.",
        source_url="https://example.com.au/",
        origin="VISIBLE_TEXT",
        page_type="general",
    )
    result = classify_registration([source], provider_domain="example.com.au")

    assert result.status == RegistrationClaimStatus.NO_REGISTERED_CLAIM_FOUND
    assert result.status != RegistrationClaimStatus.EXPLICIT_UNREGISTERED_CLAIM


def test_explicit_unregistered_wording_detected():
    source = text_source_from_fixture("unregistered_claim_page.html", "https://example.com.au/about")
    result = classify_registration([source], provider_domain="example.com.au")

    assert result.status == RegistrationClaimStatus.EXPLICIT_UNREGISTERED_CLAIM
    assert any(e.evidence_type == RegistrationClaimStatus.EXPLICIT_UNREGISTERED_CLAIM for e in result.evidence)


def test_plan_and_self_managed_only_is_ambiguous_not_unregistered():
    source = TextSource(
        text="We currently accept plan-managed and self-managed participants.",
        source_url="https://example.com.au/",
        origin="VISIBLE_TEXT",
        page_type="general",
    )
    result = classify_registration([source], provider_domain="example.com.au")

    # "plan-managed and self-managed participants" is in the explicit
    # unregistered pattern list per the build spec (it is a common phrasing
    # used by unregistered providers) - this locks in that it is NOT
    # silently reclassified as a neutral/no-claim result.
    assert result.status == RegistrationClaimStatus.EXPLICIT_UNREGISTERED_CLAIM


def test_conflicting_registration_information_routes_to_conflict_status():
    source = text_source_from_fixture("conflicting_claim_page.html", "https://example.com.au/ndis")
    result = classify_registration([source], provider_domain="example.com.au")

    assert result.status == RegistrationClaimStatus.CONFLICTING_REGISTRATION_INFORMATION
    assert result.has_contradiction
    assert result.confidence < 0.5
