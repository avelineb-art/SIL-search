"""Provider <-> official register matching cascade.

Implements the priority order from the build spec:

1. Exact ABN match (ABNs confirmed via ABN Lookup are trusted slightly
   more than an extracted-and-checksum-valid-only ABN, but both count).
2. Exact legal-entity name match (normalised, but without stripping common
   suffixes - this tier is meant to be a near-verbatim match).
3. Fuzzy match on normalised (suffix-stripped) legal name at or above the
   "probable" threshold - legal names are specific enough that a good fuzzy
   match is trustworthy on its own.
4. Fuzzy match on trading name at or above the higher "strong" threshold,
   corroborated by an overlapping state. Trading names are more likely to
   collide across unrelated organisations, so this tier both demands a
   higher score *and* independent corroboration before being trusted; the
   register import doesn't expose domain/phone/address, so state is the
   only corroboration signal available for now (see README limitations).
   A strong trading-name match *without* that corroboration is downgraded
   to MANUAL_REVIEW_REQUIRED rather than trusted outright.
5. Any remaining fuzzy match (legal or trading name) at or above the
   weaker "possible" threshold.

Anywhere a tier finds more than one equally-good candidate, matching stops
immediately with MULTIPLE_POSSIBLE_MATCHES rather than guessing between
them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rapidfuzz import fuzz

from sil_research.config import get_classification_rules
from sil_research.extraction.organisation import normalise_name
from sil_research.models import RegisterMatchStatus
from sil_research.register.base import RegisterEntryData


@dataclass(frozen=True)
class MatchCandidate:
    entry: RegisterEntryData
    score: float  # 0.0 - 1.0


@dataclass
class MatchResult:
    status: str
    confidence: float | None
    method: str
    matched_entry: RegisterEntryData | None
    candidate_entries: list[RegisterEntryData] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def _thresholds() -> dict:
    rules = get_classification_rules().get("fuzzy_matching", {})
    return {
        "strong": rules.get("strong_name_match_score", 92) / 100,
        "probable": rules.get("probable_name_match_score", 85) / 100,
        "possible": rules.get("possible_name_match_score", 75) / 100,
    }


def _score_candidates(query: str | None, entries: list[RegisterEntryData], field_getter) -> list[MatchCandidate]:
    if not query:
        return []
    scored = []
    for entry in entries:
        value = field_getter(entry)
        if not value:
            continue
        normalised_value = normalise_name(value, strip_suffixes=True)
        score = fuzz.token_sort_ratio(query, normalised_value) / 100
        scored.append(MatchCandidate(entry=entry, score=score))
    return scored


def _best_unique(candidates: list[MatchCandidate], threshold: float) -> tuple[MatchCandidate | None, bool]:
    """Returns (best_candidate_or_none, ambiguous). `ambiguous` is True when
    two or more candidates tie for the top score at/above `threshold`.
    """
    qualifying = sorted((c for c in candidates if c.score >= threshold), key=lambda c: c.score, reverse=True)
    if not qualifying:
        return None, False
    if len(qualifying) > 1 and qualifying[0].score == qualifying[1].score:
        return None, True
    return qualifying[0], False


def match_provider(
    provider_abn: str | None,
    provider_abn_confirmed: bool,
    provider_legal_name: str | None,
    provider_trading_name: str | None,
    provider_states: list[str],
    entries: list[RegisterEntryData],
) -> MatchResult:
    if not entries:
        return MatchResult(status=RegisterMatchStatus.REGISTER_NOT_CHECKED, confidence=None, method="no_snapshot", matched_entry=None)

    thresholds = _thresholds()

    # 1. Exact ABN match.
    if provider_abn:
        abn_matches = [e for e in entries if e.abn == provider_abn]
        if len(abn_matches) == 1:
            confidence = 1.0 if provider_abn_confirmed else 0.97
            return MatchResult(status=RegisterMatchStatus.EXACT_ABN_MATCH, confidence=confidence, method="exact_abn", matched_entry=abn_matches[0])
        if len(abn_matches) > 1:
            return MatchResult(
                status=RegisterMatchStatus.MULTIPLE_POSSIBLE_MATCHES,
                confidence=None,
                method="exact_abn",
                matched_entry=None,
                candidate_entries=abn_matches,
                reasons=["More than one register entry shares this ABN - the register snapshot may contain duplicate rows."],
            )

    provider_legal_exact = normalise_name(provider_legal_name) if provider_legal_name else None
    provider_legal_fuzzy = normalise_name(provider_legal_name, strip_suffixes=True) if provider_legal_name else None
    provider_trading_fuzzy = normalise_name(provider_trading_name, strip_suffixes=True) if provider_trading_name else None

    # 2. Exact (normalised, non-suffix-stripped) legal-name match.
    if provider_legal_exact:
        exact_matches = [e for e in entries if e.entity_name and normalise_name(e.entity_name) == provider_legal_exact]
        if len(exact_matches) == 1:
            return MatchResult(status=RegisterMatchStatus.EXACT_LEGAL_NAME_MATCH, confidence=0.95, method="exact_legal_name", matched_entry=exact_matches[0])
        if len(exact_matches) > 1:
            return MatchResult(
                status=RegisterMatchStatus.MULTIPLE_POSSIBLE_MATCHES,
                confidence=None,
                method="exact_legal_name",
                matched_entry=None,
                candidate_entries=exact_matches,
                reasons=["More than one register entry has an identical normalised legal name."],
            )

    # 3. Fuzzy match on normalised legal name at or above the "probable" bar.
    legal_candidates = _score_candidates(provider_legal_fuzzy, entries, lambda e: e.entity_name)
    best_legal, legal_ambiguous = _best_unique(legal_candidates, thresholds["probable"])
    if legal_ambiguous:
        return MatchResult(
            status=RegisterMatchStatus.MULTIPLE_POSSIBLE_MATCHES,
            confidence=None,
            method="fuzzy_legal_name",
            matched_entry=None,
            candidate_entries=[c.entry for c in legal_candidates if c.score >= thresholds["probable"]],
            reasons=["Multiple register entries have an equally strong fuzzy legal-name match."],
        )
    if best_legal is not None:
        return MatchResult(status=RegisterMatchStatus.PROBABLE_ENTITY_MATCH, confidence=best_legal.score, method="fuzzy_legal_name", matched_entry=best_legal.entry)

    # 4. Strong fuzzy match on trading name, corroborated by state overlap.
    trading_candidates = _score_candidates(provider_trading_fuzzy, entries, lambda e: e.trading_name or e.entity_name)
    strong_trading = sorted((c for c in trading_candidates if c.score >= thresholds["strong"]), key=lambda c: c.score, reverse=True)
    if len(strong_trading) > 1 and strong_trading[0].score == strong_trading[1].score:
        return MatchResult(
            status=RegisterMatchStatus.MULTIPLE_POSSIBLE_MATCHES,
            confidence=None,
            method="fuzzy_trading_name",
            matched_entry=None,
            candidate_entries=[c.entry for c in strong_trading],
        )
    if strong_trading:
        candidate = strong_trading[0]
        corroborated = bool(provider_states) and candidate.entry.state is not None and candidate.entry.state in provider_states
        if corroborated:
            return MatchResult(status=RegisterMatchStatus.PROBABLE_ENTITY_MATCH, confidence=candidate.score, method="fuzzy_trading_name_corroborated", matched_entry=candidate.entry)
        return MatchResult(
            status=RegisterMatchStatus.MANUAL_REVIEW_REQUIRED,
            confidence=candidate.score,
            method="fuzzy_trading_name_uncorroborated",
            matched_entry=candidate.entry,
            reasons=["Strong trading-name match but no corroborating state overlap was found - confirm manually."],
        )

    # 5. Anything else at or above the weaker "possible" threshold.
    best_possible = max(
        (c for c in (legal_candidates + trading_candidates) if c.score >= thresholds["possible"]),
        key=lambda c: c.score,
        default=None,
    )
    if best_possible is not None:
        return MatchResult(status=RegisterMatchStatus.POSSIBLE_NAME_MATCH, confidence=best_possible.score, method="fuzzy_possible", matched_entry=best_possible.entry)

    return MatchResult(status=RegisterMatchStatus.NO_CONFIDENT_MATCH, confidence=0.0, method="none", matched_entry=None)
