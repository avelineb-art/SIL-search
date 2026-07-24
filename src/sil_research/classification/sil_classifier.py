"""Explainable, rule-based SIL (Supported Independent Living) classifier.

Every rule and every point value comes from
`config/classification_rules.yml` - nothing here is hard-coded, so scores
and thresholds can be retuned without touching this module.

Design note on the negative "only provides X" rules (SDA-only, support
coordination-only, allied health-only, etc.): detecting that an
organisation *exclusively* provides another service is a semantic
judgement a keyword match can't make on its own. As a deliberate, explained
heuristic, these rules only fire when the phrase is present *and* no
genuine SIL evidence was found on a general/service page - i.e. the
keyword match is treated as the organisation's likely real service only
when nothing else on the site suggests otherwise. Likewise, SIL phrases
found only on an employment/careers page or only on a blog page are
excluded from the score and instead trigger the corresponding negative
rule, per the requirement that recruiting a SIL worker or blogging about
SIL must not, by itself, count as evidence of delivering SIL.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sil_research.classification.scoring import (
    TextSource,
    evidence_hash,
    find_phrase_occurrences,
    has_nearby_any,
    make_excerpt,
    now,
)
from sil_research.config import get_classification_rules
from sil_research.models import EvidenceItem, SilClassification


@dataclass
class RawMatch:
    rule_id: str
    description: str
    points: int
    source: TextSource
    matched_text: str
    excerpt: str


@dataclass
class SilClassificationResult:
    score: int
    classification: str
    confidence: float
    evidence: list[EvidenceItem] = field(default_factory=list)


def _rule_phrases(rule: dict) -> list[str]:
    if "phrase" in rule:
        return [rule["phrase"]]
    return rule.get("any_phrase", [])


def _match_rule(rule: dict, source: TextSource) -> list[RawMatch]:
    matches: list[RawMatch] = []
    window = rule.get("nearby_window_chars", 150)
    nearby = rule.get("requires_nearby_any")
    required_page_type = rule.get("page_type")

    if required_page_type is not None and source.page_type != required_page_type:
        return matches

    for phrase in _rule_phrases(rule):
        for occurrence in find_phrase_occurrences(source.text, phrase):
            if nearby and not has_nearby_any(source.text, occurrence.start, occurrence.end, nearby, window):
                continue
            matches.append(
                RawMatch(
                    rule_id=rule["id"],
                    description=rule["description"],
                    points=rule["points"],
                    source=source,
                    matched_text=occurrence.matched_text,
                    excerpt=make_excerpt(source.text, occurrence.start, occurrence.end),
                )
            )
    return matches


def _to_evidence(match: RawMatch, provider_domain: str) -> EvidenceItem:
    return EvidenceItem(
        evidence_type=match.rule_id,
        source_url=match.source.source_url,
        page_title=match.source.page_title,
        matched_text=match.matched_text,
        context_excerpt=match.excerpt,
        score_contribution=match.points,
        extracted_at=now(),
        origin=match.source.origin,
        document_page=match.source.document_page,
    )


def classify_sil(
    sources: list[TextSource],
    provider_domain: str = "",
    rules: dict | None = None,
) -> SilClassificationResult:
    config = rules or get_classification_rules().get("sil_classifier", {})
    positive_rules = config.get("positive_evidence", [])
    negative_rules = config.get("negative_evidence", [])
    thresholds = config.get("thresholds", {})

    general_sources = [s for s in sources if s.page_type == "general"]
    employment_sources = [s for s in sources if s.page_type == "employment"]
    blog_sources = [s for s in sources if s.page_type == "blog"]

    general_matches: list[RawMatch] = []
    for rule in positive_rules:
        for source in general_sources:
            general_matches.extend(_match_rule(rule, source))

    employment_only_matches: list[RawMatch] = []
    blog_only_matches: list[RawMatch] = []
    for rule in positive_rules:
        for source in employment_sources:
            employment_only_matches.extend(_match_rule(rule, source))
        for source in blog_sources:
            blog_only_matches.extend(_match_rule(rule, source))

    evidence: list[EvidenceItem] = []
    score = 0
    seen_hashes: set[str] = set()

    def add_evidence(match: RawMatch, category: str, points: int) -> None:
        nonlocal score
        item = _to_evidence(match, provider_domain)
        item.evidence_type = category
        item.score_contribution = points
        h = evidence_hash(provider_domain, category, item.matched_text, item.source_url)
        if h in seen_hashes:
            return
        seen_hashes.add(h)
        evidence.append(item)
        score += points

    for match in general_matches:
        add_evidence(match, match.rule_id, match.points)

    negative_by_id = {rule["id"]: rule for rule in negative_rules}

    if not general_matches and employment_only_matches:
        rule = negative_by_id.get("employment_page_sil_no_service_page")
        if rule:
            add_evidence(employment_only_matches[0], rule["id"], rule["points"])

    if not general_matches and blog_only_matches:
        rule = negative_by_id.get("blog_discussing_sil")
        if rule:
            add_evidence(blog_only_matches[0], rule["id"], rule["points"])

    exclusive_rules = [r for r in negative_rules if r.get("exclusive_signal")]
    if score <= 0:
        for rule in exclusive_rules:
            for source in sources:
                rule_matches = _match_rule(rule, source)
                if rule_matches:
                    add_evidence(rule_matches[0], rule["id"], rule["points"])
                    break

    strong = thresholds.get("strong_sil_evidence", 8)
    likely = thresholds.get("likely_sil_provider", 5)
    possible = thresholds.get("possible_sil_provider", 3)

    if score >= strong:
        classification = SilClassification.STRONG_SIL_EVIDENCE
    elif score >= likely:
        classification = SilClassification.LIKELY_SIL_PROVIDER
    elif score >= possible:
        classification = SilClassification.POSSIBLE_SIL_PROVIDER
    else:
        classification = SilClassification.INSUFFICIENT_SIL_EVIDENCE

    confidence = max(0.0, min(1.0, score / strong)) if strong else 0.0

    return SilClassificationResult(
        score=score, classification=classification, confidence=round(confidence, 2), evidence=evidence
    )
