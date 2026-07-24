"""Search-query generation.

Combines the configurable phrase library (config/search_terms.yml) with the
configurable location list (config/locations.yml), in an order driven by
the location priority list, and applies a daily budget plus a
freshness-window de-duplication check so the same query is never re-run
too soon (Google Programmable Search's free tier is 100 queries/day, and
the full phrase x location matrix is well over 400 queries per pass).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Iterable, Iterator

from sil_research.config import get_locations, get_search_terms, get_settings

# Common state name/abbreviation aliases, used only to let the CLI accept
# `--state QLD` while the location config is written in full names.
_STATE_ALIASES = {
    "nsw": "New South Wales",
    "vic": "Victoria",
    "qld": "Queensland",
    "wa": "Western Australia",
    "sa": "South Australia",
    "tas": "Tasmania",
    "act": "Australian Capital Territory",
    "nt": "Northern Territory",
    "au": "Australia",
    "australia": "Australia",
}

_METRO_BY_STATE = {
    "New South Wales": ["Sydney"],
    "Victoria": ["Melbourne"],
    "Queensland": ["Brisbane"],
    "Western Australia": ["Perth"],
    "South Australia": ["Adelaide"],
    "Tasmania": ["Hobart"],
    "Australian Capital Territory": ["Canberra"],
    "Northern Territory": ["Darwin"],
}


def normalise_state_token(token: str) -> str:
    """Map a state abbreviation or free-form name to its canonical full name."""
    key = token.strip().lower()
    return _STATE_ALIASES.get(key, token.strip())


@dataclass(frozen=True)
class CandidateQuery:
    phrase: str
    location: str
    query_text: str

    @property
    def dedup_key(self) -> tuple[str, str]:
        return (self.phrase, self.location)


class QueryGenerator:
    def __init__(
        self,
        search_terms: dict | None = None,
        locations: dict | None = None,
        daily_budget: int | None = None,
        freshness_days: int | None = None,
    ) -> None:
        self._search_terms = search_terms or get_search_terms()
        self._locations = locations or get_locations()
        settings = get_settings()
        self.daily_budget = daily_budget if daily_budget is not None else settings.sil_daily_query_budget
        self.freshness_days = (
            freshness_days if freshness_days is not None else settings.sil_query_freshness_days
        )

    def iter_phrases(self) -> Iterator[str]:
        for category_phrases in self._search_terms.values():
            yield from category_phrases

    def iter_location_tokens(self, state_filter: str | None = None) -> Iterator[str]:
        """Yield location tokens in priority order.

        Order: national -> each priority state (state name, its metro area,
        then its regional centres) -> any remaining states in the same
        pattern. `state_filter` restricts output to a single state's tokens
        (plus the national token), for the CLI's `--state` option.
        """
        priority = self._locations.get("location_priority", [])
        all_states = self._locations.get("states_and_territories", [])
        remaining_states = [s for s in all_states if s not in priority]
        regional = self._locations.get("regional_centres", {})

        wanted_state = normalise_state_token(state_filter) if state_filter else None

        def state_matches(state: str) -> bool:
            return wanted_state is None or state == wanted_state or wanted_state == "Australia"

        if wanted_state is None or wanted_state == "Australia":
            for token in self._locations.get("national", []):
                yield token

        for state in [*priority, *remaining_states]:
            if not state_matches(state):
                continue
            yield state
            for metro in _METRO_BY_STATE.get(state, []):
                yield metro
            for centre in regional.get(state, []) or []:
                yield centre

    def iter_candidate_queries(self, state_filter: str | None = None) -> Iterator[CandidateQuery]:
        phrases = list(self.iter_phrases())
        for location in self.iter_location_tokens(state_filter=state_filter):
            for phrase in phrases:
                yield CandidateQuery(
                    phrase=phrase,
                    location=location,
                    query_text=f"{phrase} {location}",
                )

    def next_batch(
        self,
        already_run: set[tuple[str, str]] | Callable[[CandidateQuery], bool] | None = None,
        budget: int | None = None,
        state_filter: str | None = None,
    ) -> list[CandidateQuery]:
        """Return the next batch of queries to run, respecting the budget and
        skipping anything already run within the freshness window.

        `already_run` may be a set of (phrase, location) keys already run
        within the freshness window, or a predicate returning True for
        queries that should be skipped.
        """
        limit = budget if budget is not None else self.daily_budget

        if already_run is None:
            should_skip: Callable[[CandidateQuery], bool] = lambda _: False
        elif isinstance(already_run, set):
            should_skip = lambda q: q.dedup_key in already_run
        else:
            should_skip = already_run

        batch: list[CandidateQuery] = []
        for candidate in self.iter_candidate_queries(state_filter=state_filter):
            if len(batch) >= limit:
                break
            if should_skip(candidate):
                continue
            batch.append(candidate)
        return batch

    def freshness_cutoff(self, now: datetime | None = None) -> datetime:
        now = now or datetime.utcnow()
        return now - timedelta(days=self.freshness_days)
