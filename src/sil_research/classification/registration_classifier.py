"""Explainable rule-based NDIS registration-language classifier.

Classifies the strength of any claim a website makes about its own NDIS
registration status. Absence of a registration statement is never treated
as evidence of being unregistered - see `RegistrationClassification
Status.NO_REGISTERED_CLAIM_FOUND`, which is a distinct, neutral outcome.

If both an explicit registered claim and an explicit unregistered claim are
found anywhere on the site, the result is
`CONFLICTING_REGISTRATION_INFORMATION` regardless of match counts, so it is
always routed to manual review rather than averaged away.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sil_research.classification.scoring import (
    TextSource,
    evidence_hash,
    find_phrase_occurrences,
    make_excerpt,
    now,
)
from sil_research.config import get_classification_rules
from sil_research.models import EvidenceItem, RegistrationClaimStatus


@dataclass
class RegistrationClassificationResult:
    status: str
    confidence: float
    evidence: list[EvidenceItem] = field(default_factory=list)
    has_contradiction: bool = False


def _find_occurrences(source: TextSource, phrases: list[str]) -> list:
    hits = []
    for phrase in phrases:
        hits.extend(find_phrase_occurrences(source.text, phrase))
    return hits


def _drop_contained_within(occurrences: list, containers: list) -> list:
    """Drop any occurrence fully contained inside one of `containers`.

    Plain substring matching means a short phrase can appear "inside" a
    longer, negated phrase - e.g. "registered NDIS provider" is a literal
    substring of "we are not a registered NDIS provider". Without this
    filter, an explicit unregistered claim would also register as an
    explicit registered claim and manufacture a false
    CONFLICTING_REGISTRATION_INFORMATION result.
    """
    return [
        occ
        for occ in occurrences
        if not any(container.start <= occ.start and occ.end <= container.end for container in containers)
    ]


def classify_registration(
    sources: list[TextSource],
    provider_domain: str = "",
    rules: dict | None = None,
) -> RegistrationClassificationResult:
    config = rules or get_classification_rules().get("registration_classifier", {})
    explicit_patterns = config.get("explicit_registered_patterns", [])
    ambiguous_patterns = config.get("ambiguous_patterns", [])
    unregistered_patterns = config.get("explicit_unregistered_patterns", [])

    evidence: list[EvidenceItem] = []
    seen_hashes: set[str] = set()

    def add(category: str, source: TextSource, matched_text: str, excerpt: str) -> None:
        h = evidence_hash(provider_domain, category, matched_text, source.source_url)
        if h in seen_hashes:
            return
        seen_hashes.add(h)
        evidence.append(
            EvidenceItem(
                evidence_type=category,
                source_url=source.source_url,
                page_title=source.page_title,
                matched_text=matched_text,
                context_excerpt=excerpt,
                score_contribution=None,
                extracted_at=now(),
                origin=source.origin,
                document_page=source.document_page,
            )
        )

    explicit_hits = 0
    ambiguous_hits = 0
    unregistered_hits = 0

    for source in sources:
        unregistered_occurrences = _find_occurrences(source, unregistered_patterns)
        registered_occurrences = _drop_contained_within(_find_occurrences(source, explicit_patterns), unregistered_occurrences)
        ambiguous_occurrences = _drop_contained_within(_find_occurrences(source, ambiguous_patterns), unregistered_occurrences)

        for occurrence in registered_occurrences:
            excerpt = make_excerpt(source.text, occurrence.start, occurrence.end)
            add(RegistrationClaimStatus.EXPLICIT_REGISTERED_CLAIM, source, occurrence.matched_text, excerpt)
            explicit_hits += 1
        for occurrence in unregistered_occurrences:
            excerpt = make_excerpt(source.text, occurrence.start, occurrence.end)
            add(RegistrationClaimStatus.EXPLICIT_UNREGISTERED_CLAIM, source, occurrence.matched_text, excerpt)
            unregistered_hits += 1
        for occurrence in ambiguous_occurrences:
            excerpt = make_excerpt(source.text, occurrence.start, occurrence.end)
            add(RegistrationClaimStatus.AMBIGUOUS_NDIS_LANGUAGE, source, occurrence.matched_text, excerpt)
            ambiguous_hits += 1

    has_contradiction = explicit_hits > 0 and unregistered_hits > 0

    if has_contradiction:
        status = RegistrationClaimStatus.CONFLICTING_REGISTRATION_INFORMATION
        confidence = 0.4
    elif explicit_hits > 0:
        status = RegistrationClaimStatus.EXPLICIT_REGISTERED_CLAIM
        confidence = 0.9
    elif unregistered_hits > 0:
        status = RegistrationClaimStatus.EXPLICIT_UNREGISTERED_CLAIM
        confidence = 0.85
    elif ambiguous_hits > 0:
        status = RegistrationClaimStatus.AMBIGUOUS_NDIS_LANGUAGE
        confidence = 0.5
    else:
        status = RegistrationClaimStatus.NO_REGISTERED_CLAIM_FOUND
        confidence = 0.7 if sources else 0.0

    return RegistrationClassificationResult(
        status=status, confidence=confidence, evidence=evidence, has_contradiction=has_contradiction
    )
