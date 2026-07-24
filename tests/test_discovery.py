from __future__ import annotations

from sil_research.discovery.domain_filter import canonical_domain, classify_domain, dedupe_by_domain
from sil_research.discovery.job_board import extract_employer_from_job_ad
from sil_research.discovery.query_generator import CandidateQuery, QueryGenerator, normalise_state_token


def test_canonical_domain_strips_scheme_and_www():
    assert canonical_domain("https://www.example.com.au/services") == "example.com.au"
    assert canonical_domain("http://example.com.au/") == "example.com.au"


def test_dedupe_by_domain_keeps_first_seen_url():
    urls = [
        "https://example.com.au/",
        "https://www.example.com.au/services",
        "https://another-provider.org.au/",
    ]
    deduped = dedupe_by_domain(urls)
    assert deduped == {
        "example.com.au": "https://example.com.au/",
        "another-provider.org.au": "https://another-provider.org.au/",
    }


def test_classify_domain_excludes_social_media():
    decision = classify_domain("https://www.facebook.com/somepage")
    assert decision.excluded is True


def test_classify_domain_excludes_pdf_only_results():
    decision = classify_domain("https://example.com.au/files/report.pdf")
    assert decision.excluded is True


def test_classify_domain_flags_job_board_without_excluding():
    decision = classify_domain("https://www.seek.com.au/job/12345")
    assert decision.is_job_board is True
    assert decision.excluded is False


def test_classify_domain_allows_ordinary_provider_site():
    decision = classify_domain("https://sunriseliving.com.au/services")
    assert decision.excluded is False
    assert decision.is_job_board is False
    assert decision.is_generic_directory is False


def test_extract_employer_from_job_ad_finds_website_and_keeps_hint_name():
    ad_text = "Sunrise Living is hiring! Visit https://sunriseliving.com.au/careers to apply."
    employer = extract_employer_from_job_ad(ad_text, employer_name_hint="Sunrise Living")
    assert employer.employer_name == "Sunrise Living"
    assert employer.employer_website == "https://sunriseliving.com.au/careers"


def test_extract_employer_from_job_ad_without_url_returns_none_website():
    employer = extract_employer_from_job_ad("Apply in person at our office.")
    assert employer.employer_website is None


def test_normalise_state_token_maps_abbreviations():
    assert normalise_state_token("qld") == "Queensland"
    assert normalise_state_token("NSW") == "New South Wales"
    assert normalise_state_token("Tasmania") == "Tasmania"


def test_query_generator_respects_daily_budget():
    search_terms = {"service_synonyms": ['"supported independent living" NDIS', '"SIL provider" NDIS']}
    locations = {
        "location_priority": ["New South Wales"],
        "national": ["Australia"],
        "states_and_territories": ["New South Wales"],
        "regional_centres": {"New South Wales": []},
    }
    generator = QueryGenerator(search_terms=search_terms, locations=locations, daily_budget=3, freshness_days=30)
    batch = generator.next_batch()
    assert len(batch) == 3
    assert all(isinstance(q, CandidateQuery) for q in batch)


def test_query_generator_skips_already_run_queries():
    search_terms = {"service_synonyms": ['"supported independent living" NDIS']}
    locations = {
        "location_priority": ["New South Wales"],
        "national": ["Australia"],
        "states_and_territories": ["New South Wales"],
        "regional_centres": {"New South Wales": []},
    }
    generator = QueryGenerator(search_terms=search_terms, locations=locations, daily_budget=10, freshness_days=30)

    already_run = {('"supported independent living" NDIS', "Australia")}
    batch = generator.next_batch(already_run=already_run)

    assert all(q.dedup_key != ('"supported independent living" NDIS', "Australia") for q in batch)
    assert any(q.location == "New South Wales" for q in batch)


def test_query_generator_state_filter_restricts_locations():
    search_terms = {"service_synonyms": ['"SIL provider" NDIS']}
    locations = {
        "location_priority": ["New South Wales", "Victoria"],
        "national": ["Australia"],
        "states_and_territories": ["New South Wales", "Victoria"],
        "regional_centres": {"New South Wales": ["Newcastle"], "Victoria": ["Geelong"]},
    }
    generator = QueryGenerator(search_terms=search_terms, locations=locations, daily_budget=50, freshness_days=30)

    batch = generator.next_batch(state_filter="NSW")
    locations_seen = {q.location for q in batch}
    assert "Newcastle" in locations_seen
    assert "Geelong" not in locations_seen
