"""Central configuration loading.

Credentials and environment-specific values come from environment
variables (via `.env`, loaded with python-dotenv). Rule tables, phrase
lists and other non-secret configuration live in the YAML files under
`config/` so they can be tuned without a code change.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"

load_dotenv(PROJECT_ROOT / ".env", override=False)


class Settings(BaseSettings):
    """Environment-derived settings. See `.env.example` for documentation."""

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    sil_database_url: str = "sqlite:///data/processed/sil_research.db"

    sil_search_provider: str = "mock"
    google_cse_api_key: str | None = None
    google_cse_cx: str | None = None
    serpapi_api_key: str | None = None
    serpapi_google_domain: str = "google.com.au"
    serpapi_country: str = "au"
    serpapi_language: str = "en"
    sil_daily_query_budget: int = 100
    sil_query_freshness_days: int = 30

    abn_lookup_guid: str | None = None

    sil_crawler_user_agent: str = (
        "SILProviderResearchBot/0.1 (+mailto:research@example.org)"
    )
    sil_crawl_delay_seconds: float = 2.0
    sil_crawl_max_pages_per_domain: int = 25
    sil_crawl_concurrency: int = 4
    sil_crawl_request_timeout_seconds: float = 15.0

    sil_export_dir: str = "data/exports"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def _load_yaml(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@lru_cache
def get_search_terms() -> dict[str, Any]:
    return _load_yaml("search_terms.yml")


@lru_cache
def get_locations() -> dict[str, Any]:
    return _load_yaml("locations.yml")


@lru_cache
def get_classification_rules() -> dict[str, Any]:
    return _load_yaml("classification_rules.yml")


@lru_cache
def get_app_settings() -> dict[str, Any]:
    return _load_yaml("settings.yml")


@lru_cache
def get_register_columns() -> dict[str, Any]:
    return _load_yaml("register_columns.yml")


def resolve_data_path(relative: str) -> Path:
    """Resolve a path from settings/env relative to the project root."""
    path = Path(relative)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path
