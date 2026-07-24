"""Search-provider interface.

Kept deliberately narrow so any compliant, authorised search API (Google
Programmable Search, Bing Web Search, Brave Search, etc.) can be dropped in
behind it. Nothing outside this module should know which concrete provider
is in use.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class SearchProviderError(Exception):
    """Base class for search-provider failures."""


class SearchQuotaExceededError(SearchProviderError):
    """Raised when the provider's own quota/budget has been exhausted."""


@dataclass(frozen=True)
class SearchResult:
    url: str
    title: str
    snippet: str
    rank: int
    query: str


class SearchProvider(ABC):
    """Interface every discovery source must implement."""

    @abstractmethod
    def search(self, query: str, start: int = 0, limit: int = 10) -> list[SearchResult]:
        """Return up to `limit` results for `query`, starting at offset `start`.

        Implementations must raise `SearchQuotaExceededError` when the
        provider reports a quota/rate-limit failure so callers can stop
        cleanly rather than retrying into a wall.
        """
        raise NotImplementedError
