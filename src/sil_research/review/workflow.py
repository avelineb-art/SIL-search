"""Automated segmentation and the manual-review queue.

Segmentation never asserts a legal conclusion - it only routes a provider
into one of the labelled buckets from the build spec so a human reviewer
knows what to look at first. Register-match-dependent segments (B, C) will
mostly resolve to segment A/D in Stage 2, since register matching doesn't
exist until Stage 3 and `register_match_status` defaults to
`REGISTER_NOT_CHECKED` - that default is intentionally treated the same as
an unconfirmed match by segment A, never as "unregistered".
"""

from __future__ import annotations

from sil_research.models import (
    AutomatedSegment,
    ManualReviewStatus,
    ProviderRecord,
    RegisterMatchStatus,
    RegistrationClaimStatus,
    SilClassification,
)

_STRONG_OR_LIKELY_SIL = {SilClassification.STRONG_SIL_EVIDENCE, SilClassification.LIKELY_SIL_PROVIDER}
_NO_OR_AMBIGUOUS_CLAIM = {
    RegistrationClaimStatus.NO_REGISTERED_CLAIM_FOUND,
    RegistrationClaimStatus.AMBIGUOUS_NDIS_LANGUAGE,
}
_UNCONFIRMED_REGISTER_MATCH = {
    RegisterMatchStatus.NO_CONFIDENT_MATCH,
    RegisterMatchStatus.REGISTER_NOT_CHECKED,
    RegisterMatchStatus.POSSIBLE_NAME_MATCH,
    RegisterMatchStatus.MULTIPLE_POSSIBLE_MATCHES,
    RegisterMatchStatus.MANUAL_REVIEW_REQUIRED,
}
_CONFIDENT_REGISTER_MATCH = {
    RegisterMatchStatus.EXACT_ABN_MATCH,
    RegisterMatchStatus.EXACT_LEGAL_NAME_MATCH,
    RegisterMatchStatus.PROBABLE_ENTITY_MATCH,
}


def compute_automated_segment(provider: ProviderRecord) -> str:
    """Assigns exactly one of the five build-spec segments, evaluated in the
    order they're listed in the spec (explicit unregistered claim and
    explicit registration claim both take priority over SIL-evidence
    ambiguity, since they describe the registration-language finding
    itself rather than the SIL classification).
    """
    if provider.registration_claim_status == RegistrationClaimStatus.EXPLICIT_UNREGISTERED_CLAIM:
        return AutomatedSegment.EXPLICIT_UNREGISTERED_PROVIDER_CLAIM

    if (
        provider.registration_claim_status == RegistrationClaimStatus.EXPLICIT_REGISTERED_CLAIM
        and provider.register_match_status in _UNCONFIRMED_REGISTER_MATCH
    ):
        return AutomatedSegment.WEBSITE_REGISTRATION_CLAIM_REQUIRES_VERIFICATION

    if provider.sil_classification == SilClassification.POSSIBLE_SIL_PROVIDER:
        return AutomatedSegment.SIL_SERVICE_CLASSIFICATION_REQUIRES_REVIEW

    if provider.sil_classification in _STRONG_OR_LIKELY_SIL:
        if (
            provider.registration_claim_status in _NO_OR_AMBIGUOUS_CLAIM
            and provider.register_match_status in _CONFIDENT_REGISTER_MATCH
        ):
            return AutomatedSegment.REGISTERED_SIL_PROVIDER_NO_CLEAR_WEBSITE_REGISTRATION_STATEMENT

        if provider.registration_claim_status in _NO_OR_AMBIGUOUS_CLAIM and (
            provider.register_match_status in _UNCONFIRMED_REGISTER_MATCH
            or provider.register_match_status == RegisterMatchStatus.NO_CONFIDENT_MATCH
        ):
            return AutomatedSegment.SIL_PROVIDER_NO_PUBLIC_REGISTRATION_STATEMENT_REGISTER_STATUS_UNCONFIRMED

    return AutomatedSegment.UNSEGMENTED


def apply_segmentation(provider: ProviderRecord) -> ProviderRecord:
    provider.automated_segment = compute_automated_segment(provider)
    if provider.manual_review_status == ManualReviewStatus.PENDING and provider.automated_segment != AutomatedSegment.UNSEGMENTED:
        # Every populated segment is, per spec, subject to manual review -
        # this just keeps the default status explicit rather than implicit.
        provider.manual_review_status = ManualReviewStatus.PENDING
    return provider
