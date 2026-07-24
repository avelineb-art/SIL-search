from __future__ import annotations

from datetime import datetime

from sil_research.classification.scoring import TextSource
from sil_research.classification.sil_classifier import classify_sil
from sil_research.models import SilClassification
from tests.conftest import text_source_from_fixture


def test_exact_sil_phrase_scores_and_records_evidence():
    source = text_source_from_fixture("strong_sil_page.html", "https://example.com.au/services")
    result = classify_sil([source], provider_domain="example.com.au")

    assert result.classification == SilClassification.STRONG_SIL_EVIDENCE
    assert result.score >= 8
    exact_matches = [e for e in result.evidence if e.evidence_type == "exact_sil_phrase"]
    assert exact_matches, "expected the exact SIL phrase rule to fire"
    assert exact_matches[0].source_url == "https://example.com.au/services"
    assert "Supported Independent Living" in exact_matches[0].context_excerpt.replace("…", "")


def test_sil_acronym_requires_nearby_context():
    now = datetime.utcnow()
    source_with_context = TextSource(
        text="Our SIL homes support NDIS participants with daily living.",
        source_url="https://example.com.au/",
        origin="VISIBLE_TEXT",
        page_type="general",
    )
    source_without_context = TextSource(
        text="Please mail your SIL application form to our office in Bendigo.",
        source_url="https://example.com.au/forms",
        origin="VISIBLE_TEXT",
        page_type="general",
    )

    with_context = classify_sil([source_with_context], provider_domain="example.com.au")
    without_context = classify_sil([source_without_context], provider_domain="example.com.au")

    assert any(e.evidence_type == "sil_acronym_near_context" for e in with_context.evidence)
    assert not any(e.evidence_type == "sil_acronym_near_context" for e in without_context.evidence)


def test_sil_vacancy_detection():
    source = TextSource(
        text="We currently have a SIL vacancy in our Ballarat home - apply today.",
        source_url="https://example.com.au/sil-vacancies",
        origin="VISIBLE_TEXT",
        page_type="general",
    )
    result = classify_sil([source], provider_domain="example.com.au")
    assert any(e.evidence_type == "sil_vacancy" for e in result.evidence)


def test_24_7_residential_support_evidence():
    source = TextSource(
        text="Our shared homes provide 24/7 support with a dedicated rostered team.",
        source_url="https://example.com.au/services",
        origin="VISIBLE_TEXT",
        page_type="general",
    )
    result = classify_sil([source], provider_domain="example.com.au")
    assert any(e.evidence_type == "overnight_24_7_support" for e in result.evidence)


def test_sda_only_exclusion_when_no_other_sil_evidence():
    source = text_source_from_fixture("sda_only_page.html", "https://example.com.au/sda")
    result = classify_sil([source], provider_domain="example.com.au")

    assert result.classification == SilClassification.INSUFFICIENT_SIL_EVIDENCE
    assert result.score < 0
    assert any(e.evidence_type == "sda_only" for e in result.evidence)


def test_support_coordination_only_exclusion():
    source = text_source_from_fixture("support_coordination_only_page.html", "https://example.com.au/support-coordination")
    result = classify_sil([source], provider_domain="example.com.au")

    assert result.classification == SilClassification.INSUFFICIENT_SIL_EVIDENCE
    assert any(e.evidence_type == "support_coordination_only" for e in result.evidence)


def test_blog_only_mention_is_not_positive_evidence():
    source = text_source_from_fixture("blog_sil_page.html", "https://example.com.au/blog/understanding-sil", page_type=None)
    result = classify_sil([source], provider_domain="example.com.au")

    assert result.classification == SilClassification.INSUFFICIENT_SIL_EVIDENCE
    assert result.score < 0
    assert any(e.evidence_type == "blog_discussing_sil" for e in result.evidence)


def test_job_ad_only_mention_is_not_positive_evidence():
    source = text_source_from_fixture("job_ad_employment_page.html", "https://example.com.au/careers/sil-support-worker", page_type=None)
    result = classify_sil([source], provider_domain="example.com.au")

    assert result.classification == SilClassification.INSUFFICIENT_SIL_EVIDENCE
    assert any(e.evidence_type == "employment_page_sil_no_service_page" for e in result.evidence)


def test_job_ad_mention_does_not_suppress_genuine_service_page_evidence():
    job_source = text_source_from_fixture("job_ad_employment_page.html", "https://example.com.au/careers/sil-support-worker", page_type=None)
    service_source = text_source_from_fixture("strong_sil_page.html", "https://example.com.au/services")

    result = classify_sil([job_source, service_source], provider_domain="example.com.au")

    assert result.classification == SilClassification.STRONG_SIL_EVIDENCE
    assert not any(e.evidence_type == "employment_page_sil_no_service_page" for e in result.evidence)


def test_evidence_hash_deduplicates_identical_matches_across_calls():
    source = text_source_from_fixture("strong_sil_page.html", "https://example.com.au/services")
    result = classify_sil([source, source], provider_domain="example.com.au")

    exact_matches = [e for e in result.evidence if e.evidence_type == "exact_sil_phrase" and e.source_url == source.source_url]
    assert len(exact_matches) == 1
