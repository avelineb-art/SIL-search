"""Internal review dashboard (Stage 4).

Run with: `streamlit run dashboard/app.py` from the project root (with the
dashboard extra installed: `pip install -e ".[dashboard]"`).

All querying/mutation logic lives in dashboard/data.py, which has no
Streamlit dependency and is unit-tested - this file is just rendering and
wiring button clicks to those functions.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import data as dash_data  # noqa: E402 - path setup above must run first

from sil_research.database import init_db, session_scope  # noqa: E402
from sil_research.models import ManualReviewStatus  # noqa: E402

st.set_page_config(page_title="SIL Provider Research - Review Dashboard", layout="wide")
init_db()


def _classification_notice() -> None:
    st.caption(
        "Every value on this page is an automated finding for manual review - none of it is a "
        "legal conclusion about any organisation's registration status. Registration rules can "
        "change, providers may be mid-application, website wording can be outdated, and a "
        "register snapshot may not reflect a recent change. Confirm anything before acting on it."
    )


def _filters_sidebar() -> dash_data.ProviderFilters:
    st.sidebar.header("Filters")
    state = st.sidebar.text_input("State (e.g. NSW)", value="").strip().upper() or None
    keyword = st.sidebar.text_input("Keyword search (name/domain)", value="").strip() or None
    min_score = st.sidebar.slider("Minimum SIL score", min_value=-10, max_value=30, value=-10)

    sil_classification = st.sidebar.selectbox(
        "SIL classification",
        ["(any)", "STRONG_SIL_EVIDENCE", "LIKELY_SIL_PROVIDER", "POSSIBLE_SIL_PROVIDER", "INSUFFICIENT_SIL_EVIDENCE"],
    )
    registration_claim_status = st.sidebar.selectbox(
        "Registration-claim status",
        [
            "(any)",
            "EXPLICIT_REGISTERED_CLAIM",
            "AMBIGUOUS_NDIS_LANGUAGE",
            "NO_REGISTERED_CLAIM_FOUND",
            "EXPLICIT_UNREGISTERED_CLAIM",
            "CONFLICTING_REGISTRATION_INFORMATION",
        ],
    )
    register_match_status = st.sidebar.selectbox(
        "Register-match status",
        [
            "(any)",
            "EXACT_ABN_MATCH",
            "EXACT_LEGAL_NAME_MATCH",
            "PROBABLE_ENTITY_MATCH",
            "POSSIBLE_NAME_MATCH",
            "MULTIPLE_POSSIBLE_MATCHES",
            "NO_CONFIDENT_MATCH",
            "REGISTER_NOT_CHECKED",
            "MANUAL_REVIEW_REQUIRED",
        ],
    )
    automated_segment = st.sidebar.selectbox(
        "Automated segment",
        [
            "(any)",
            "SIL_PROVIDER_NO_PUBLIC_REGISTRATION_STATEMENT_REGISTER_STATUS_UNCONFIRMED",
            "REGISTERED_SIL_PROVIDER_NO_CLEAR_WEBSITE_REGISTRATION_STATEMENT",
            "WEBSITE_REGISTRATION_CLAIM_REQUIRES_VERIFICATION",
            "SIL_SERVICE_CLASSIFICATION_REQUIRES_REVIEW",
            "EXPLICIT_UNREGISTERED_PROVIDER_CLAIM",
            "UNSEGMENTED",
        ],
    )

    return dash_data.ProviderFilters(
        state=state,
        keyword=keyword,
        min_sil_score=min_score if min_score > -10 else None,
        sil_classification=None if sil_classification == "(any)" else sil_classification,
        registration_claim_status=None if registration_claim_status == "(any)" else registration_claim_status,
        register_match_status=None if register_match_status == "(any)" else register_match_status,
        automated_segment=None if automated_segment == "(any)" else automated_segment,
    )


def _render_provider_table(rows: list[dict]) -> str | None:
    st.subheader(f"Providers ({len(rows)})")
    if not rows:
        st.info("No providers match the current filters.")
        return None

    import pandas as pd

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.download_button(
        "Download current view as CSV",
        data=df.to_csv(index=False).encode("utf-8"),
        file_name="providers_current_view.csv",
        mime="text/csv",
    )
    if st.button("Run full export (providers + evidence CSV)"):
        path = dash_data.run_full_csv_export()
        st.success(f"Full export written to {path}")

    return st.selectbox("Select a provider to review", options=[r["provider_id"] for r in rows])


def _render_sil_evidence_section(detail: dash_data.ProviderDetail) -> None:
    st.markdown("### 1. Evidence the organisation provides SIL")
    p = detail.provider
    st.write(f"**Score:** {p.sil_score}  **Classification:** {p.sil_classification}  **Confidence:** {p.sil_confidence:.2f}")
    if not detail.sil_evidence:
        st.caption("No SIL evidence recorded.")
    for item in detail.sil_evidence:
        with st.container(border=True):
            st.write(f"**{item.evidence_category}** ({item.score_contribution:+d} points)" if item.score_contribution is not None else f"**{item.evidence_category}**")
            st.write(f"> {item.context_excerpt}")
            st.caption(f"[{item.page_title or item.source_url}]({item.source_url}) · {item.origin}")


def _render_registration_section(detail: dash_data.ProviderDetail) -> None:
    st.markdown("### 2. Website statements about NDIS registration")
    p = detail.provider
    st.write(f"**Status:** {p.registration_claim_status}  **Confidence:** {p.registration_claim_confidence:.2f}")
    if not detail.registration_evidence:
        st.caption("No registration-language evidence recorded.")
    for item in detail.registration_evidence:
        with st.container(border=True):
            st.write(f"**{item.claim_status}**")
            st.write(f"> {item.context_excerpt}")
            st.caption(f"[{item.page_title or item.source_url}]({item.source_url}) · {item.origin}")


def _render_register_match_section(detail: dash_data.ProviderDetail) -> None:
    st.markdown("### 3. Official provider-register match")
    p = detail.provider
    st.write(f"**Status:** {p.register_match_status}")
    if p.register_match_status not in ("REGISTER_NOT_CHECKED",):
        st.write(
            f"**Confidence:** {p.register_match_confidence}  **Register entity name:** {p.register_entity_name or '-'}  "
            f"**Register ABN:** {p.register_abn or '-'}  **Registration status:** {p.register_registration_status or '-'}"
        )
        st.write(f"**Snapshot date:** {p.register_snapshot_date}")
    if p.register_match_status == "NO_CONFIDENT_MATCH":
        st.warning("No confident register match - this must NOT be read as 'unregistered'.")
    if detail.register_matches:
        st.caption("Match history:")
        for m in detail.register_matches:
            st.write(f"- {m.matched_at}: {m.method}, confidence {m.confidence}, snapshot {m.snapshot_id}")


def _render_automated_interpretation_section(detail: dash_data.ProviderDetail) -> None:
    st.markdown("### 4. Automated interpretation")
    p = detail.provider
    st.write(f"**Segment:** `{p.automated_segment}`")
    st.write(f"**Discovery:** {p.discovery_source or '-'} via `{p.discovery_query or '-'}`")
    st.write(f"**Last website check:** {p.last_website_check}  **Last register check:** {p.last_register_check}")
    st.write(f"**ABN:** {p.abn or '-'} (valid checksum: {p.abn_valid}, ABN Lookup confirmed: {p.abn_lookup_confirmed})")

    if detail.errors:
        with st.expander(f"Crawl/processing errors ({len(detail.errors)})"):
            for err in detail.errors:
                st.write(f"`{err.occurred_at}` **{err.stage}/{err.error_type}**: {err.message}")

    if detail.documents:
        with st.expander(f"PDF documents ({len(detail.documents)})"):
            for doc in detail.documents:
                status_icon = "✅" if doc.extraction_status == "OK" else "⚠️"
                st.write(f"{status_icon} [{doc.doc_type or 'unknown'}]({doc.source_url}) - {doc.extraction_status}" + (f" ({doc.error})" if doc.error else ""))


def _render_human_review_section(detail: dash_data.ProviderDetail) -> None:
    st.markdown("### 5. Human-review decision")
    p = detail.provider
    st.write(f"**Current status:** `{p.manual_review_status}`")
    if p.reviewer_notes:
        st.write(f"**Notes:** {p.reviewer_notes}")

    if detail.review_decisions:
        with st.expander(f"Review history ({len(detail.review_decisions)})"):
            for d in detail.review_decisions:
                st.write(f"`{d.decided_at}` **{d.decision}** by {d.reviewer or 'unknown'} - {d.notes or ''}")

    with st.form(key=f"review_form_{p.provider_id}"):
        reviewer = st.text_input("Reviewer name/ID")
        decision = st.selectbox("Decision", [status.value for status in ManualReviewStatus if status != ManualReviewStatus.PENDING])
        notes = st.text_area("Reviewer notes")
        submitted = st.form_submit_button("Save review decision")
        if submitted:
            with session_scope() as session:
                dash_data.record_review_decision(session, p.provider_id, reviewer, decision, notes)
            st.success("Review decision saved.")
            st.rerun()

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("Recrawl this provider"):
            with st.spinner("Recrawling..."):
                dash_data.recrawl_provider(p.domain)
            st.success("Recrawl complete.")
            st.rerun()
    with col2:
        if st.button("Re-run classification"):
            dash_data.reclassify_provider(p.domain)
            st.success("Classification re-run.")
            st.rerun()
    with col3:
        if st.button("Mark false positive", type="secondary"):
            with session_scope() as session:
                dash_data.mark_false_positive(session, p.provider_id, reviewer="dashboard")
            st.success("Marked as not a SIL provider.")
            st.rerun()


def _render_change_comparison(provider_id: str, domain: str) -> None:
    st.markdown("### Compare website changes between crawl dates")
    with session_scope() as session:
        urls = dash_data.get_page_urls(session, provider_id)
        if not urls:
            st.caption("No crawl history yet.")
            return
        selected_url = st.selectbox("Page URL", urls, key=f"diff_url_{provider_id}")
        snapshots = dash_data.get_page_snapshots(session, provider_id, selected_url)
        if len(snapshots) < 2:
            st.caption("Only one crawl of this page so far - nothing to compare yet.")
            return

        labels = [f"{s.fetched_at} (run {s.crawl_run_id})" for s in snapshots]
        col1, col2 = st.columns(2)
        with col1:
            older_idx = st.selectbox("Older snapshot", range(len(snapshots)), format_func=lambda i: labels[i], index=0, key=f"older_{provider_id}")
        with col2:
            newer_idx = st.selectbox("Newer snapshot", range(len(snapshots)), format_func=lambda i: labels[i], index=len(snapshots) - 1, key=f"newer_{provider_id}")

        older_text = snapshots[older_idx].text.visible_text if snapshots[older_idx].text else ""
        newer_text = snapshots[newer_idx].text.visible_text if snapshots[newer_idx].text else ""
        diff = dash_data.diff_page_text(older_text, newer_text, labels[older_idx], labels[newer_idx])
        if diff:
            st.code(diff, language="diff")
        else:
            st.caption("No text difference between these two snapshots.")


def main() -> None:
    st.title("SIL Provider Research - Review Dashboard")
    _classification_notice()

    filters = _filters_sidebar()
    with session_scope() as session:
        rows = dash_data.list_providers(session, filters)

    selected_id = _render_provider_table(rows)
    if not selected_id:
        return

    st.divider()
    with session_scope() as session:
        detail = dash_data.get_provider_detail(session, selected_id)
        if detail is None:
            st.error("Provider not found.")
            return

        st.header(detail.provider.trading_name or detail.provider.domain)
        st.caption(f"[{detail.provider.website_url}]({detail.provider.website_url})")

        _render_sil_evidence_section(detail)
        _render_registration_section(detail)
        _render_register_match_section(detail)
        _render_automated_interpretation_section(detail)
        _render_human_review_section(detail)

    st.divider()
    _render_change_comparison(selected_id, detail.provider.domain)


main()
