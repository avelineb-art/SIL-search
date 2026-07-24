from __future__ import annotations

from sil_research.models import (
    AutomatedSegment,
    ProviderRecord,
    RegisterMatchStatus,
    RegistrationClaimStatus,
    SilClassification,
)
from sil_research.review.workflow import compute_automated_segment


def _provider(**overrides) -> ProviderRecord:
    defaults = dict(provider_id="p1", domain="example.com.au", website_url="https://example.com.au/")
    defaults.update(overrides)
    return ProviderRecord(**defaults)


def test_segment_a_strong_sil_no_claim_register_unconfirmed():
    p = _provider(
        sil_classification=SilClassification.STRONG_SIL_EVIDENCE,
        registration_claim_status=RegistrationClaimStatus.NO_REGISTERED_CLAIM_FOUND,
        register_match_status=RegisterMatchStatus.REGISTER_NOT_CHECKED,
    )
    assert (
        compute_automated_segment(p)
        == AutomatedSegment.SIL_PROVIDER_NO_PUBLIC_REGISTRATION_STATEMENT_REGISTER_STATUS_UNCONFIRMED
    )


def test_segment_b_registered_sil_provider_no_website_claim():
    p = _provider(
        sil_classification=SilClassification.LIKELY_SIL_PROVIDER,
        registration_claim_status=RegistrationClaimStatus.AMBIGUOUS_NDIS_LANGUAGE,
        register_match_status=RegisterMatchStatus.EXACT_ABN_MATCH,
    )
    assert compute_automated_segment(p) == AutomatedSegment.REGISTERED_SIL_PROVIDER_NO_CLEAR_WEBSITE_REGISTRATION_STATEMENT


def test_segment_c_website_claim_requires_verification():
    p = _provider(
        sil_classification=SilClassification.STRONG_SIL_EVIDENCE,
        registration_claim_status=RegistrationClaimStatus.EXPLICIT_REGISTERED_CLAIM,
        register_match_status=RegisterMatchStatus.NO_CONFIDENT_MATCH,
    )
    assert compute_automated_segment(p) == AutomatedSegment.WEBSITE_REGISTRATION_CLAIM_REQUIRES_VERIFICATION


def test_segment_d_possible_sil_requires_review():
    p = _provider(
        sil_classification=SilClassification.POSSIBLE_SIL_PROVIDER,
        registration_claim_status=RegistrationClaimStatus.NO_REGISTERED_CLAIM_FOUND,
    )
    assert compute_automated_segment(p) == AutomatedSegment.SIL_SERVICE_CLASSIFICATION_REQUIRES_REVIEW


def test_segment_e_explicit_unregistered_claim_takes_priority():
    p = _provider(
        sil_classification=SilClassification.STRONG_SIL_EVIDENCE,
        registration_claim_status=RegistrationClaimStatus.EXPLICIT_UNREGISTERED_CLAIM,
    )
    assert compute_automated_segment(p) == AutomatedSegment.EXPLICIT_UNREGISTERED_PROVIDER_CLAIM


def test_insufficient_sil_evidence_is_unsegmented_not_negative_claim():
    p = _provider(
        sil_classification=SilClassification.INSUFFICIENT_SIL_EVIDENCE,
        registration_claim_status=RegistrationClaimStatus.NO_REGISTERED_CLAIM_FOUND,
    )
    assert compute_automated_segment(p) == AutomatedSegment.UNSEGMENTED
