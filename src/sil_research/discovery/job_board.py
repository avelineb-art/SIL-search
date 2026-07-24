"""Job-board discovery pointer handling.

SIL support-worker and house-based job ads are one of the strongest
discovery signals available (an organisation recruiting for a specific SIL
house is very likely delivering SIL directly). Per the build spec, job-ad
*content* must never be used as SIL classification evidence - only as a
pointer to the employer's own website, which is then crawled and
classified normally.

Live, automated fetching of job-board listing pages is intentionally NOT
wired up yet: Seek, Indeed and EthicalJobs each have their own terms of
service governing automated access, and those need to be confirmed
per-board before any scraper runs against them. This module only provides
the (offline, text-in/text-out) extraction helper for when ad text is
already available - e.g. pasted manually, or sourced later through a
board's own permitted API/export - so the rest of the discovery pipeline
(domain filtering, crawling, classification) can already treat a job ad's
employer link as a normal discovery source once that text exists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_URL_PATTERN = re.compile(r"https?://[^\s)>\]\"']+")


@dataclass(frozen=True)
class JobAdEmployer:
    employer_name: str | None
    employer_website: str | None


def extract_employer_from_job_ad(ad_text: str, employer_name_hint: str | None = None) -> JobAdEmployer:
    """Best-effort extraction of an employer name and website from job-ad text.

    This is deliberately conservative: it only pulls a URL if one is
    present in the text, and only claims an employer name if a hint was
    supplied by the caller (e.g. the board's own structured "company name"
    field) - it does not attempt to guess a company name out of free text,
    since a wrong guess here would misattribute evidence to the wrong
    organisation later in the pipeline.
    """
    urls = _URL_PATTERN.findall(ad_text)
    website = urls[0] if urls else None
    return JobAdEmployer(employer_name=employer_name_hint, employer_website=website)
