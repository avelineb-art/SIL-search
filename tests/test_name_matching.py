from __future__ import annotations

from sil_research.extraction.organisation import find_copyright_name, find_legal_name_candidates, normalise_name


def test_normalise_name_lowercases_and_strips_punctuation():
    assert normalise_name("Sunrise Living Pty. Ltd.") == "sunrise living pty ltd"


def test_normalise_name_standardises_ampersand():
    assert normalise_name("Smith & Jones Care Services") == "smith and jones care services"


def test_normalise_name_collapses_whitespace():
    assert normalise_name("  Sunrise   Living   ") == "sunrise living"


def test_normalise_name_retains_suffix_by_default():
    assert normalise_name("Sunrise Living Pty Ltd") == "sunrise living pty ltd"


def test_normalise_name_strips_suffix_when_requested():
    assert normalise_name("Sunrise Living Pty Ltd", strip_suffixes=True) == "sunrise living"


def test_normalise_name_does_not_over_strip_distinguishing_words():
    # Two genuinely different organisations should not collide just
    # because one has a strippable suffix - the distinguishing word
    # ("Living" vs "Disability") must survive suffix stripping.
    a = normalise_name("Sunrise Living Services", strip_suffixes=True)
    b = normalise_name("Sunrise Disability Services", strip_suffixes=True)
    assert a != b


def test_normalise_name_strips_multiple_trailing_suffixes():
    result = normalise_name("Sunrise Disability Services Group Pty Ltd", strip_suffixes=True)
    assert "pty ltd" not in result
    assert "group" not in result
    assert result.startswith("sunrise")


def test_find_legal_name_candidates_matches_pty_ltd_suffix():
    text = "This service is operated by Sunrise Living Pty Ltd under NDIS arrangements."
    candidates = find_legal_name_candidates(text, source="page_text")
    names = [c.name for c in candidates]
    assert "Sunrise Living Pty Ltd" in names


def test_find_copyright_name_from_footer():
    footer = "© 2026 Sunrise Living Pty Ltd. All rights reserved."
    assert find_copyright_name(footer) == "Sunrise Living Pty Ltd"


def test_find_copyright_name_returns_none_when_absent():
    assert find_copyright_name("Contact us for more information.") is None
