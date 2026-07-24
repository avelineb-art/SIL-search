from __future__ import annotations

from sil_research.crawling.pdf_parser import classify_document_type, extract_pdf_text


def test_classify_document_type_brochure():
    assert classify_document_type("https://example.com.au/files/brochure.pdf", "Our Service Brochure") == "brochure"


def test_classify_document_type_privacy_policy():
    assert classify_document_type("https://example.com.au/privacy-policy.pdf", "Privacy Policy") == "privacy_policy"


def test_classify_document_type_service_agreement():
    assert classify_document_type("https://example.com.au/docs/agreement.pdf", "Service Agreement 2026") == "service_agreement"


def test_classify_document_type_unknown_defaults_to_other():
    assert classify_document_type("https://example.com.au/misc.pdf", "Random content") == "other"


def test_extract_pdf_text_flags_corrupt_pdf_as_extraction_failure():
    result = extract_pdf_text(b"this is not a real pdf file", source_url="https://example.com.au/broken.pdf")
    assert result.extraction_failed is True
    assert result.error is not None
    assert result.pages == []
