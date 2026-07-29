from __future__ import annotations

from sil_research.extraction.abn import find_abns, format_abn, is_valid_abn_checksum


def test_valid_abn_checksum():
    # 51 824 753 556 is the ABR's own published example of a valid ABN.
    assert is_valid_abn_checksum("51824753556") is True


def test_invalid_abn_checksum():
    assert is_valid_abn_checksum("51824753557") is False


def test_invalid_abn_wrong_length():
    assert is_valid_abn_checksum("123456789") is False
    assert is_valid_abn_checksum("abcdefghijk") is False


def test_find_abns_with_spaced_format():
    text = "We are proud members of the sector. ABN 51 824 753 556. Call us today."
    matches = find_abns(text)
    assert len(matches) == 1
    assert matches[0].digits == "51824753556"
    assert matches[0].valid_checksum is True


def test_find_abns_with_colon_and_no_spaces():
    text = "Company details - ABN: 51824753556"
    matches = find_abns(text)
    assert matches[0].digits == "51824753556"


def test_find_abns_with_full_name_prefix():
    text = "Australian Business Number 51 824 753 556 is registered to this entity."
    matches = find_abns(text)
    assert matches[0].digits == "51824753556"


def test_find_abns_deduplicates_repeated_number():
    text = "ABN 51 824 753 556. Later in the footer: ABN: 51824753556."
    matches = find_abns(text)
    assert len(matches) == 1


def test_find_abns_flags_invalid_checksum_but_still_returns_it():
    text = "ABN 12 345 678 901 (placeholder, not a real ABN)"
    matches = find_abns(text)
    assert len(matches) == 1
    assert matches[0].valid_checksum is False


def test_find_abns_with_dotted_prefix():
    text = "Company details - A.B.N. 51 824 753 556"
    matches = find_abns(text)
    assert len(matches) == 1
    assert matches[0].digits == "51824753556"


def test_find_abns_with_hyphenated_digit_groups():
    text = "ABN: 51-824-753-556"
    matches = find_abns(text)
    assert len(matches) == 1
    assert matches[0].digits == "51824753556"


def test_format_abn():
    assert format_abn("51824753556") == "51 824 753 556"
