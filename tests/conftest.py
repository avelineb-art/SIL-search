from __future__ import annotations

from pathlib import Path

import pytest

from sil_research.classification.scoring import TextSource, classify_page_type
from sil_research.crawling.page_parser import parse_page

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture_html(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def text_source_from_fixture(name: str, url: str, base_domain: str = "example.com.au", page_type: str | None = None) -> TextSource:
    """Parse a synthetic HTML fixture through the real page_parser and wrap
    its visible text as a TextSource, so classifier tests exercise the same
    HTML->text path the crawler uses in production.
    """
    html = load_fixture_html(name)
    parsed = parse_page(html, page_url=url, base_domain=base_domain)
    resolved_page_type = page_type or classify_page_type(url, parsed.title)
    return TextSource(
        text=parsed.visible_text,
        source_url=url,
        origin="VISIBLE_TEXT",
        page_title=parsed.title,
        page_type=resolved_page_type,
    )


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR
