from __future__ import annotations

from sil_research.extraction.contact import extract_general_email, extract_phone_numbers
from sil_research.extraction.locations import extract_service_locations, extract_states, postcode_to_state


def test_extract_phone_numbers_landline():
    text = "Call us on (02) 9999 8888 for more information."
    phones = extract_phone_numbers(text)
    assert len(phones) == 1


def test_extract_phone_numbers_mobile():
    text = "Reach the on-call coordinator on 0412 345 678."
    phones = extract_phone_numbers(text)
    assert len(phones) == 1


def test_extract_phone_numbers_1300_number():
    text = "For enquiries phone 1300 555 123 during business hours."
    phones = extract_phone_numbers(text)
    assert len(phones) == 1


def test_extract_phone_numbers_deduplicates():
    text = "Phone: 02 9999 8888. Also reachable on 0299998888."
    phones = extract_phone_numbers(text)
    assert len(phones) == 1


def test_extract_general_email_prefers_generic_local_part_on_own_domain():
    text = "Contact jane.smith@sunriseliving.com.au or info@sunriseliving.com.au for enquiries."
    email = extract_general_email(text, domain="sunriseliving.com.au")
    assert email == "info@sunriseliving.com.au"


def test_extract_general_email_falls_back_to_own_domain_when_no_generic_part():
    text = "Contact jane.smith@sunriseliving.com.au for enquiries."
    email = extract_general_email(text, domain="sunriseliving.com.au")
    assert email == "jane.smith@sunriseliving.com.au"


def test_extract_general_email_returns_none_when_absent():
    assert extract_general_email("No contact details published here.") is None


def test_postcode_to_state_nsw():
    assert postcode_to_state("2150") == "NSW"


def test_postcode_to_state_qld():
    assert postcode_to_state("4000") == "QLD"


def test_postcode_to_state_unmapped_returns_none():
    assert postcode_to_state("0001") is None


def test_extract_states_detects_abbreviation_and_full_name():
    text = "We operate across NSW and also in Victoria."
    assert set(extract_states(text)) == {"NSW", "VIC"}


def test_extract_service_locations_state_then_postcode():
    text = "Our office is located at 12 Smith St, Parramatta NSW 2150."
    mentions = extract_service_locations(text)
    assert any(m.state == "NSW" and m.postcode == "2150" for m in mentions)


def test_extract_service_locations_postcode_then_state():
    text = "Serving 3220 VIC and surrounding suburbs."
    mentions = extract_service_locations(text)
    assert any(m.state == "VIC" and m.postcode == "3220" for m in mentions)
