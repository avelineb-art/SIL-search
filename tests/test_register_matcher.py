from __future__ import annotations

from sil_research.models import RegisterMatchStatus
from sil_research.register.base import RegisterEntryData
from sil_research.register.matcher import match_provider


def _entry(entry_id=1, abn=None, entity_name=None, trading_name=None, state=None, registration_status="Active") -> RegisterEntryData:
    return RegisterEntryData(
        entry_id=entry_id,
        abn=abn,
        entity_name=entity_name,
        trading_name=trading_name,
        registration_status=registration_status,
        registration_groups=[],
        registration_expiry=None,
        state=state,
    )


def test_no_entries_means_register_not_checked():
    result = match_provider(
        provider_abn="51824753556", provider_abn_confirmed=False, provider_legal_name="Sunrise Living Pty Ltd",
        provider_trading_name="Sunrise Living", provider_states=["NSW"], entries=[],
    )
    assert result.status == RegisterMatchStatus.REGISTER_NOT_CHECKED
    assert result.confidence is None


def test_exact_abn_match_is_highest_priority_even_with_different_name():
    entries = [_entry(abn="51824753556", entity_name="Totally Different Name Pty Ltd")]
    result = match_provider(
        provider_abn="51824753556", provider_abn_confirmed=True, provider_legal_name="Sunrise Living Pty Ltd",
        provider_trading_name="Sunrise Living", provider_states=["NSW"], entries=entries,
    )
    assert result.status == RegisterMatchStatus.EXACT_ABN_MATCH
    assert result.confidence == 1.0
    assert result.matched_entry.entity_name == "Totally Different Name Pty Ltd"


def test_unconfirmed_abn_match_gets_slightly_lower_confidence():
    entries = [_entry(abn="51824753556", entity_name="Sunrise Living Pty Ltd")]
    result = match_provider(
        provider_abn="51824753556", provider_abn_confirmed=False, provider_legal_name="Sunrise Living Pty Ltd",
        provider_trading_name=None, provider_states=[], entries=entries,
    )
    assert result.status == RegisterMatchStatus.EXACT_ABN_MATCH
    assert result.confidence < 1.0


def test_duplicate_abn_in_register_is_multiple_possible_matches():
    entries = [_entry(entry_id=1, abn="51824753556", entity_name="A"), _entry(entry_id=2, abn="51824753556", entity_name="B")]
    result = match_provider(
        provider_abn="51824753556", provider_abn_confirmed=True, provider_legal_name="A", provider_trading_name=None,
        provider_states=[], entries=entries,
    )
    assert result.status == RegisterMatchStatus.MULTIPLE_POSSIBLE_MATCHES


def test_exact_legal_name_match_without_abn():
    entries = [_entry(abn="99999999999", entity_name="Sunrise Living Pty Ltd")]
    result = match_provider(
        provider_abn=None, provider_abn_confirmed=False, provider_legal_name="Sunrise Living Pty Ltd",
        provider_trading_name=None, provider_states=[], entries=entries,
    )
    assert result.status == RegisterMatchStatus.EXACT_LEGAL_NAME_MATCH
    assert result.matched_entry.abn == "99999999999"


def test_strong_fuzzy_legal_name_match():
    entries = [_entry(abn="99999999999", entity_name="Sunrise Living Proprietary Limited")]
    result = match_provider(
        provider_abn=None, provider_abn_confirmed=False, provider_legal_name="Sunrise Living Pty Ltd",
        provider_trading_name=None, provider_states=[], entries=entries,
    )
    assert result.status == RegisterMatchStatus.PROBABLE_ENTITY_MATCH
    assert result.confidence >= 0.85


def test_ambiguous_fuzzy_legal_name_match_is_multiple_possible_matches():
    entries = [
        _entry(entry_id=1, abn="11111111111", entity_name="Sunrise Living Group Pty Ltd"),
        _entry(entry_id=2, abn="22222222222", entity_name="Sunrise Living Ltd"),
    ]
    # "Group Pty Ltd" and "Ltd" both fully strip away, so both candidates
    # normalise down to the identical string "sunrise living" - an exact
    # tie against the query - and matching must refuse to guess between
    # two different ABNs rather than silently picking the first.
    result = match_provider(
        provider_abn=None, provider_abn_confirmed=False, provider_legal_name="Sunrise Living Care Pty Ltd",
        provider_trading_name=None, provider_states=[], entries=entries,
    )
    assert result.status == RegisterMatchStatus.MULTIPLE_POSSIBLE_MATCHES
    assert {e.entry_id for e in result.candidate_entries} == {1, 2}


def test_strong_trading_name_match_corroborated_by_state():
    entries = [_entry(abn="99999999999", entity_name="Meadowview Community Services Ltd", trading_name="Sunrise Living", state="NSW")]
    result = match_provider(
        provider_abn=None, provider_abn_confirmed=False, provider_legal_name=None, provider_trading_name="Sunrise Living",
        provider_states=["NSW"], entries=entries,
    )
    assert result.status == RegisterMatchStatus.PROBABLE_ENTITY_MATCH
    assert result.method == "fuzzy_trading_name_corroborated"


def test_strong_trading_name_match_without_state_corroboration_needs_manual_review():
    entries = [_entry(abn="99999999999", entity_name="Meadowview Community Services Ltd", trading_name="Sunrise Living", state="QLD")]
    result = match_provider(
        provider_abn=None, provider_abn_confirmed=False, provider_legal_name=None, provider_trading_name="Sunrise Living",
        provider_states=["NSW"], entries=entries,
    )
    assert result.status == RegisterMatchStatus.MANUAL_REVIEW_REQUIRED


def test_weak_fuzzy_match_is_possible_name_match():
    # "Sunshine Living Services" is close enough to "Sunrise Living" to be
    # worth a look, but not close enough to trust outright.
    entries = [_entry(abn="99999999999", entity_name="Sunshine Living Services")]
    result = match_provider(
        provider_abn=None, provider_abn_confirmed=False, provider_legal_name="Sunrise Living",
        provider_trading_name=None, provider_states=[], entries=entries,
    )
    assert result.status == RegisterMatchStatus.POSSIBLE_NAME_MATCH


def test_no_match_when_names_are_unrelated():
    entries = [_entry(abn="99999999999", entity_name="Completely Unrelated Enterprises Pty Ltd")]
    result = match_provider(
        provider_abn=None, provider_abn_confirmed=False, provider_legal_name="Sunrise Living Pty Ltd",
        provider_trading_name="Sunrise Living", provider_states=[], entries=entries,
    )
    assert result.status == RegisterMatchStatus.NO_CONFIDENT_MATCH
    assert result.confidence == 0.0
