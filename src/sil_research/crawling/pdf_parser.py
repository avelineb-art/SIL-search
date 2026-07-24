"""PDF text extraction for provider-owned/provider-linked documents only.

Only ever called on PDFs discovered as same-domain links from a provider's
own website (see page_parser.parse_page, which already restricts pdf_links
to the same registrable domain) - this module does not fetch or decide
which PDFs are in scope, it only extracts text from bytes already deemed
in scope by the caller.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pdfplumber

logger = logging.getLogger(__name__)

_DOC_TYPE_KEYWORDS = {
    "brochure": ["brochure", "flyer", "fact sheet", "factsheet"],
    "service_agreement": ["service agreement", "service agreements"],
    "privacy_policy": ["privacy policy", "privacy statement"],
    "participant_handbook": ["participant handbook", "resident handbook", "welcome handbook"],
    "complaints_policy": ["complaints policy", "complaints procedure", "feedback and complaints"],
}


@dataclass
class PdfPageText:
    page_number: int
    text: str


@dataclass
class ParsedPdf:
    pages: list[PdfPageText] = field(default_factory=list)
    doc_type: str = "unknown"
    extraction_failed: bool = False
    error: str | None = None

    @property
    def full_text(self) -> str:
        return "\n".join(p.text for p in self.pages)


def classify_document_type(source_url: str, sample_text: str) -> str:
    haystack = f"{source_url.lower()} {sample_text[:2000].lower()}"
    for doc_type, keywords in _DOC_TYPE_KEYWORDS.items():
        if any(keyword in haystack for keyword in keywords):
            return doc_type
    return "other"


def extract_pdf_text(pdf_bytes: bytes, source_url: str, max_pages: int = 40) -> ParsedPdf:
    """Extract text page-by-page so evidence can cite a page number.

    Extraction failures (encrypted, corrupt, scanned-image-only PDFs) are
    returned as a flagged result rather than raised, so one bad document
    never stops the crawl of a provider's other pages.
    """
    try:
        import io

        pages: list[PdfPageText] = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for index, page in enumerate(pdf.pages[:max_pages], start=1):
                text = page.extract_text() or ""
                pages.append(PdfPageText(page_number=index, text=text))
    except Exception as exc:  # pdfplumber/pdfminer raise a variety of exception types
        logger.warning("PDF extraction failed for %s: %s", source_url, exc)
        return ParsedPdf(extraction_failed=True, error=str(exc))

    sample_text = pages[0].text if pages else ""
    doc_type = classify_document_type(source_url, sample_text)

    if not any(p.text.strip() for p in pages):
        return ParsedPdf(
            pages=pages,
            doc_type=doc_type,
            extraction_failed=True,
            error="No extractable text found (likely a scanned/image-only PDF)",
        )

    return ParsedPdf(pages=pages, doc_type=doc_type)
