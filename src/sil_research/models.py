"""Plain dataclasses used as the in-memory shape for provider records and
evidence. These are deliberately separate from the SQLAlchemy ORM models in
`database.py`: classifiers and extractors operate on these dataclasses so
their logic has no persistence dependency and stays trivially testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum


class SilClassification(StrEnum):
    STRONG_SIL_EVIDENCE = "STRONG_SIL_EVIDENCE"
    LIKELY_SIL_PROVIDER = "LIKELY_SIL_PROVIDER"
    POSSIBLE_SIL_PROVIDER = "POSSIBLE_SIL_PROVIDER"
    INSUFFICIENT_SIL_EVIDENCE = "INSUFFICIENT_SIL_EVIDENCE"


class RegistrationClaimStatus(StrEnum):
    EXPLICIT_REGISTERED_CLAIM = "EXPLICIT_REGISTERED_CLAIM"
    AMBIGUOUS_NDIS_LANGUAGE = "AMBIGUOUS_NDIS_LANGUAGE"
    NO_REGISTERED_CLAIM_FOUND = "NO_REGISTERED_CLAIM_FOUND"
    EXPLICIT_UNREGISTERED_CLAIM = "EXPLICIT_UNREGISTERED_CLAIM"
    CONFLICTING_REGISTRATION_INFORMATION = "CONFLICTING_REGISTRATION_INFORMATION"


class RegisterMatchStatus(StrEnum):
    EXACT_ABN_MATCH = "EXACT_ABN_MATCH"
    EXACT_LEGAL_NAME_MATCH = "EXACT_LEGAL_NAME_MATCH"
    PROBABLE_ENTITY_MATCH = "PROBABLE_ENTITY_MATCH"
    POSSIBLE_NAME_MATCH = "POSSIBLE_NAME_MATCH"
    MULTIPLE_POSSIBLE_MATCHES = "MULTIPLE_POSSIBLE_MATCHES"
    NO_CONFIDENT_MATCH = "NO_CONFIDENT_MATCH"
    REGISTER_NOT_CHECKED = "REGISTER_NOT_CHECKED"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"


class RegisterRegistrationStatus(StrEnum):
    APPROVED = "APPROVED"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"
    APPLICATION_OR_TRANSITION_STATUS_UNKNOWN = "APPLICATION_OR_TRANSITION_STATUS_UNKNOWN"
    NOT_FOUND = "NOT_FOUND"
    UNKNOWN = "UNKNOWN"


class AutomatedSegment(StrEnum):
    SIL_PROVIDER_NO_PUBLIC_REGISTRATION_STATEMENT_REGISTER_STATUS_UNCONFIRMED = (
        "SIL_PROVIDER_NO_PUBLIC_REGISTRATION_STATEMENT_REGISTER_STATUS_UNCONFIRMED"
    )
    REGISTERED_SIL_PROVIDER_NO_CLEAR_WEBSITE_REGISTRATION_STATEMENT = (
        "REGISTERED_SIL_PROVIDER_NO_CLEAR_WEBSITE_REGISTRATION_STATEMENT"
    )
    WEBSITE_REGISTRATION_CLAIM_REQUIRES_VERIFICATION = (
        "WEBSITE_REGISTRATION_CLAIM_REQUIRES_VERIFICATION"
    )
    SIL_SERVICE_CLASSIFICATION_REQUIRES_REVIEW = "SIL_SERVICE_CLASSIFICATION_REQUIRES_REVIEW"
    EXPLICIT_UNREGISTERED_PROVIDER_CLAIM = "EXPLICIT_UNREGISTERED_PROVIDER_CLAIM"
    UNSEGMENTED = "UNSEGMENTED"


class ManualReviewStatus(StrEnum):
    CONFIRMED_SIL_PROVIDER = "CONFIRMED_SIL_PROVIDER"
    NOT_A_SIL_PROVIDER = "NOT_A_SIL_PROVIDER"
    SDA_ONLY = "SDA_ONLY"
    PROPERTY_OR_TENANCY_ONLY = "PROPERTY_OR_TENANCY_ONLY"
    SUPPORT_COORDINATION_ONLY = "SUPPORT_COORDINATION_ONLY"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    REGISTRATION_CLAIM_CONFIRMED = "REGISTRATION_CLAIM_CONFIRMED"
    NO_REGISTRATION_CLAIM_CONFIRMED = "NO_REGISTRATION_CLAIM_CONFIRMED"
    REGISTER_MATCH_CONFIRMED = "REGISTER_MATCH_CONFIRMED"
    REGISTER_MATCH_REJECTED = "REGISTER_MATCH_REJECTED"
    NEEDS_FURTHER_RESEARCH = "NEEDS_FURTHER_RESEARCH"
    PENDING = "PENDING"


class EvidenceOrigin(StrEnum):
    VISIBLE_TEXT = "VISIBLE_TEXT"
    METADATA = "METADATA"
    STRUCTURED_DATA = "STRUCTURED_DATA"
    PDF_TEXT = "PDF_TEXT"


@dataclass
class EvidenceItem:
    """A single piece of matched evidence backing a classification score."""

    evidence_type: str
    source_url: str
    page_title: str | None
    matched_text: str
    context_excerpt: str
    score_contribution: int | None
    extracted_at: datetime
    origin: EvidenceOrigin = EvidenceOrigin.VISIBLE_TEXT
    document_page: int | None = None


@dataclass
class ProviderRecord:
    """In-memory representation of a researched organisation."""

    provider_id: str
    trading_name: str | None = None
    legal_name: str | None = None
    normalised_trading_name: str | None = None
    normalised_legal_name: str | None = None
    abn: str | None = None
    abn_valid: bool | None = None
    abn_lookup_confirmed: bool | None = None
    acn: str | None = None
    domain: str = ""
    website_url: str = ""
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    states: list[str] = field(default_factory=list)
    service_locations: list[str] = field(default_factory=list)

    sil_score: int = 0
    sil_classification: str = SilClassification.INSUFFICIENT_SIL_EVIDENCE
    sil_confidence: float = 0.0
    sil_evidence: list[EvidenceItem] = field(default_factory=list)

    registration_claim_status: str = RegistrationClaimStatus.NO_REGISTERED_CLAIM_FOUND
    registration_claim_confidence: float = 0.0
    registration_evidence: list[EvidenceItem] = field(default_factory=list)

    register_match_status: str = RegisterMatchStatus.REGISTER_NOT_CHECKED
    register_registration_status: str | None = None
    register_match_confidence: float | None = None
    register_entity_name: str | None = None
    register_abn: str | None = None
    register_snapshot_date: date | None = None
    register_evidence: list[EvidenceItem] = field(default_factory=list)

    discovery_query: str | None = None
    discovery_source: str | None = None
    crawled_urls: list[str] = field(default_factory=list)

    last_website_check: datetime | None = None
    last_register_check: datetime | None = None

    automated_segment: str = AutomatedSegment.UNSEGMENTED
    manual_review_status: str = ManualReviewStatus.PENDING
    reviewer_notes: str | None = None
