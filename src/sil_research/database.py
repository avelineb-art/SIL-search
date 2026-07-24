"""SQLAlchemy schema and session management.

SQLite is used for local development; the same models work unchanged
against PostgreSQL by pointing `SIL_DATABASE_URL` at a Postgres DSN
(production hardening, e.g. connection pooling tuned for Postgres, is a
Stage 5 concern - the schema itself is already portable).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Iterator

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

from sil_research.config import get_settings, resolve_data_path


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.utcnow()


class Provider(Base):
    __tablename__ = "providers"

    provider_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trading_name: Mapped[str | None] = mapped_column(String(255))
    legal_name: Mapped[str | None] = mapped_column(String(255))
    normalised_trading_name: Mapped[str | None] = mapped_column(String(255))
    normalised_legal_name: Mapped[str | None] = mapped_column(String(255))
    abn: Mapped[str | None] = mapped_column(String(11), unique=True)
    abn_valid: Mapped[bool | None] = mapped_column(Boolean)
    abn_lookup_confirmed: Mapped[bool | None] = mapped_column(Boolean)
    acn: Mapped[str | None] = mapped_column(String(9))
    domain: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    website_url: Mapped[str] = mapped_column(String(2048))
    phone: Mapped[str | None] = mapped_column(String(64))
    email: Mapped[str | None] = mapped_column(String(255))
    address: Mapped[str | None] = mapped_column(String(512))
    states: Mapped[list] = mapped_column(JSON, default=list)
    service_locations: Mapped[list] = mapped_column(JSON, default=list)

    sil_score: Mapped[int] = mapped_column(Integer, default=0)
    sil_classification: Mapped[str] = mapped_column(String(64), default="INSUFFICIENT_SIL_EVIDENCE")
    sil_confidence: Mapped[float] = mapped_column(Float, default=0.0)

    registration_claim_status: Mapped[str] = mapped_column(
        String(64), default="NO_REGISTERED_CLAIM_FOUND"
    )
    registration_claim_confidence: Mapped[float] = mapped_column(Float, default=0.0)

    register_match_status: Mapped[str] = mapped_column(String(64), default="REGISTER_NOT_CHECKED")
    register_registration_status: Mapped[str | None] = mapped_column(String(64))
    register_match_confidence: Mapped[float | None] = mapped_column(Float)
    register_entity_name: Mapped[str | None] = mapped_column(String(255))
    register_abn: Mapped[str | None] = mapped_column(String(11))
    register_snapshot_date: Mapped[datetime | None] = mapped_column(DateTime)

    discovery_query: Mapped[str | None] = mapped_column(Text)
    discovery_source: Mapped[str | None] = mapped_column(String(128))
    crawl_status: Mapped[str] = mapped_column(String(32), default="PENDING")

    last_website_check: Mapped[datetime | None] = mapped_column(DateTime)
    last_register_check: Mapped[datetime | None] = mapped_column(DateTime)

    automated_segment: Mapped[str] = mapped_column(String(128), default="UNSEGMENTED")
    manual_review_status: Mapped[str] = mapped_column(String(64), default="PENDING")
    reviewer_notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    pages: Mapped[list["Page"]] = relationship(back_populates="provider", cascade="all, delete-orphan")
    documents: Mapped[list["Document"]] = relationship(back_populates="provider", cascade="all, delete-orphan")
    sil_evidence: Mapped[list["SilEvidence"]] = relationship(back_populates="provider", cascade="all, delete-orphan")
    registration_evidence: Mapped[list["RegistrationEvidence"]] = relationship(
        back_populates="provider", cascade="all, delete-orphan"
    )


class Domain(Base):
    __tablename__ = "domains"

    domain_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_domain: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    provider_id: Mapped[str | None] = mapped_column(ForeignKey("providers.provider_id"))
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    exclusion_reason: Mapped[str | None] = mapped_column(String(255))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class Page(Base):
    __tablename__ = "pages"
    __table_args__ = (UniqueConstraint("provider_id", "canonical_url", name="uq_page_provider_canonical_url"),)

    page_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("providers.provider_id"))
    url: Mapped[str] = mapped_column(String(2048))
    canonical_url: Mapped[str] = mapped_column(String(2048))
    http_status: Mapped[int | None] = mapped_column(Integer)
    page_title: Mapped[str | None] = mapped_column(String(512))
    meta_description: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    error: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    page_type: Mapped[str | None] = mapped_column(String(32))

    provider: Mapped[Provider] = relationship(back_populates="pages")
    text: Mapped["PageText | None"] = relationship(back_populates="page", uselist=False, cascade="all, delete-orphan")


class PageText(Base):
    __tablename__ = "page_text"

    page_text_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("pages.page_id"), unique=True)
    visible_text: Mapped[str | None] = mapped_column(Text)
    headings: Mapped[list] = mapped_column(JSON, default=list)
    footer_text: Mapped[str | None] = mapped_column(Text)
    structured_data: Mapped[dict] = mapped_column(JSON, default=dict)

    page: Mapped[Page] = relationship(back_populates="text")


class Document(Base):
    __tablename__ = "documents"

    document_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("providers.provider_id"))
    source_url: Mapped[str] = mapped_column(String(2048))
    doc_type: Mapped[str | None] = mapped_column(String(64))
    extraction_status: Mapped[str] = mapped_column(String(32), default="PENDING")
    extracted_text: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    error: Mapped[str | None] = mapped_column(Text)

    provider: Mapped[Provider] = relationship(back_populates="documents")


class SilEvidence(Base):
    __tablename__ = "sil_evidence"

    evidence_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("providers.provider_id"))
    page_id: Mapped[int | None] = mapped_column(ForeignKey("pages.page_id"))
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.document_id"))
    evidence_category: Mapped[str] = mapped_column(String(128))
    matched_text: Mapped[str] = mapped_column(Text)
    context_excerpt: Mapped[str] = mapped_column(Text)
    score_contribution: Mapped[int | None] = mapped_column(Integer)
    origin: Mapped[str] = mapped_column(String(32), default="VISIBLE_TEXT")
    source_url: Mapped[str] = mapped_column(String(2048))
    page_title: Mapped[str | None] = mapped_column(String(512))
    document_page: Mapped[int | None] = mapped_column(Integer)
    extracted_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    evidence_hash: Mapped[str] = mapped_column(String(64), index=True)

    provider: Mapped[Provider] = relationship(back_populates="sil_evidence")


class RegistrationEvidence(Base):
    __tablename__ = "registration_evidence"

    evidence_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("providers.provider_id"))
    page_id: Mapped[int | None] = mapped_column(ForeignKey("pages.page_id"))
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.document_id"))
    claim_status: Mapped[str] = mapped_column(String(64))
    matched_text: Mapped[str] = mapped_column(Text)
    context_excerpt: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(32), default="VISIBLE_TEXT")
    source_url: Mapped[str] = mapped_column(String(2048))
    page_title: Mapped[str | None] = mapped_column(String(512))
    page_type: Mapped[str | None] = mapped_column(String(32))
    document_page: Mapped[int | None] = mapped_column(Integer)
    extracted_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    evidence_hash: Mapped[str] = mapped_column(String(64), index=True)

    provider: Mapped[Provider] = relationship(back_populates="registration_evidence")


class SearchQuery(Base):
    __tablename__ = "search_queries"

    query_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query_text: Mapped[str] = mapped_column(String(512))
    phrase: Mapped[str] = mapped_column(String(255))
    location: Mapped[str] = mapped_column(String(128))
    run_at: Mapped[datetime | None] = mapped_column(DateTime)
    result_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    __table_args__ = (UniqueConstraint("phrase", "location", "run_at", name="uq_query_phrase_location_run"),)


class SearchResult(Base):
    __tablename__ = "search_results"

    result_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query_id: Mapped[int] = mapped_column(ForeignKey("search_queries.query_id"))
    url: Mapped[str] = mapped_column(String(2048), unique=True)
    title: Mapped[str | None] = mapped_column(String(512))
    snippet: Mapped[str | None] = mapped_column(Text)
    rank: Mapped[int | None] = mapped_column(Integer)
    canonical_domain: Mapped[str | None] = mapped_column(String(255))
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    exclusion_reason: Mapped[str | None] = mapped_column(String(255))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class CrawlRun(Base):
    __tablename__ = "crawl_runs"

    run_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("providers.provider_id"))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    pages_crawled: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="RUNNING")


class ErrorRecord(Base):
    __tablename__ = "errors"

    error_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_id: Mapped[str | None] = mapped_column(ForeignKey("providers.provider_id"))
    stage: Mapped[str] = mapped_column(String(64))
    error_type: Mapped[str] = mapped_column(String(128))
    message: Mapped[str] = mapped_column(Text)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class ReviewDecision(Base):
    __tablename__ = "review_decisions"

    decision_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("providers.provider_id"))
    reviewer: Mapped[str | None] = mapped_column(String(255))
    decision: Mapped[str] = mapped_column(String(64))
    notes: Mapped[str | None] = mapped_column(Text)
    prior_automated_segment: Mapped[str | None] = mapped_column(String(128))
    decided_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class ExportRun(Base):
    __tablename__ = "export_runs"

    export_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    format: Mapped[str] = mapped_column(String(16))
    row_count: Mapped[int] = mapped_column(Integer)
    file_path: Mapped[str] = mapped_column(String(1024))
    run_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


# --- Stage 3 tables (schema defined now per the approved Stage 1 spec;
# populated once the register importer and ABN Lookup client land). ---


class ProviderRegisterSnapshot(Base):
    __tablename__ = "provider_register_snapshots"

    snapshot_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_file: Mapped[str] = mapped_column(String(1024))
    download_date: Mapped[datetime] = mapped_column(DateTime)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    row_count: Mapped[int] = mapped_column(Integer)
    column_manifest: Mapped[dict] = mapped_column(JSON, default=dict)


class ProviderRegisterMatch(Base):
    __tablename__ = "provider_register_matches"

    match_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("providers.provider_id"))
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("provider_register_snapshots.snapshot_id"))
    method: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column(Float)
    register_entity_name: Mapped[str | None] = mapped_column(String(255))
    register_abn: Mapped[str | None] = mapped_column(String(11))
    registration_status: Mapped[str | None] = mapped_column(String(64))
    registration_groups: Mapped[list] = mapped_column(JSON, default=list)
    registration_expiry: Mapped[datetime | None] = mapped_column(DateTime)
    matched_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class AbnLookupCache(Base):
    __tablename__ = "abn_lookup_cache"

    abn: Mapped[str] = mapped_column(String(11), primary_key=True)
    response_json: Mapped[dict] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    ttl_expires_at: Mapped[datetime | None] = mapped_column(DateTime)


_engine = None
_SessionFactory: sessionmaker | None = None


def get_engine(database_url: str | None = None):
    global _engine
    if _engine is None or database_url is not None:
        url = database_url or get_settings().sil_database_url
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        if url.startswith("sqlite:///") and not url.startswith("sqlite:////"):
            db_path = url.replace("sqlite:///", "", 1)
            resolve_data_path(db_path).parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(url, connect_args=connect_args)
    return _engine


def init_db(database_url: str | None = None) -> None:
    engine = get_engine(database_url)
    Base.metadata.create_all(engine)


def get_session_factory(database_url: str | None = None) -> sessionmaker:
    global _SessionFactory
    if _SessionFactory is None or database_url is not None:
        _SessionFactory = sessionmaker(bind=get_engine(database_url), expire_on_commit=False)
    return _SessionFactory


@contextmanager
def session_scope(database_url: str | None = None) -> Iterator[Session]:
    factory = get_session_factory(database_url)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
