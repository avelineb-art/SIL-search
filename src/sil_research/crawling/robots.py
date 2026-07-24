"""robots.txt handling.

One fetch per domain, cached for the lifetime of the process. If robots.txt
cannot be fetched (missing, timeout, non-200), the domain is treated as
fully allowed - that is the standard robots.txt convention, but the failure
is still surfaced to the caller so it can be logged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

logger = logging.getLogger(__name__)


@dataclass
class RobotsResult:
    allowed: bool
    fetch_error: str | None = None


class RobotsChecker:
    def __init__(self, client: httpx.Client | None = None, timeout: float = 10.0) -> None:
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=True)
        self._parsers: dict[str, tuple[RobotFileParser, str | None]] = {}

    def _robots_url(self, url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}/robots.txt"

    def _get_parser(self, url: str) -> tuple[RobotFileParser, str | None]:
        robots_url = self._robots_url(url)
        if robots_url in self._parsers:
            return self._parsers[robots_url]

        parser = RobotFileParser()
        parser.set_url(robots_url)
        fetch_error: str | None = None
        try:
            response = self._client.get(robots_url)
            if response.status_code == 200:
                parser.parse(response.text.splitlines())
            elif response.status_code == 404:
                parser.parse([])  # no robots.txt => allow all
            else:
                fetch_error = f"robots.txt returned HTTP {response.status_code}"
                parser.parse([])
        except httpx.HTTPError as exc:
            fetch_error = f"robots.txt fetch failed: {exc}"
            parser.parse([])

        self._parsers[robots_url] = (parser, fetch_error)
        return self._parsers[robots_url]

    def can_fetch(self, url: str, user_agent: str) -> RobotsResult:
        parser, fetch_error = self._get_parser(url)
        try:
            allowed = parser.can_fetch(user_agent, url)
        except Exception as exc:  # pragma: no cover - defensive, robotparser is normally safe
            logger.warning("robots.txt evaluation failed for %s: %s", url, exc)
            return RobotsResult(allowed=True, fetch_error=str(exc))
        return RobotsResult(allowed=allowed, fetch_error=fetch_error)

    def crawl_delay(self, url: str, user_agent: str) -> float | None:
        parser, _ = self._get_parser(url)
        delay = parser.crawl_delay(user_agent)
        return float(delay) if delay is not None else None
