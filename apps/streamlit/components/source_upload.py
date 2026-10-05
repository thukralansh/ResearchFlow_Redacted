"""Primary Paper upload component backed only by the FastAPI contract."""

import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import streamlit as st
from apps.streamlit.api_client import ResearchFlowApiClient
from apps.streamlit.components.extraction_execution import render_extraction_execution
from apps.streamlit.components.processing_status import render_source_processing
from apps.streamlit.components.repository_snapshot import render_repository_snapshot


def primary_paper_upload_form(
    *,
    research_question_id: object,
    filename: str,
    title: str,
    authors: str,
    identifiers_json: str,
    relationship_rationale: str,
    relevance_scope: str,
    confidence: float | Decimal,
    replace_current: bool,
) -> dict[str, str]:
    """Build the exact multipart fields after enforcing required UI metadata."""
    normalized_title = title.strip() or Path(filename).stem.strip()
    normalized_rationale = relationship_rationale.strip()
    if not normalized_title:
        raise ValueError("Enter a paper title before storing the Primary Paper.")
    if not normalized_rationale:
        raise ValueError("Explain why this source is the Primary Paper before storing it.")

    form = {
        "research_question_id": str(research_question_id),
        "title": normalized_title,
        "authors_json": json.dumps(
            [author.strip() for author in authors.splitlines() if author.strip()]
        ),
        "identifiers_json": identifiers_json,
        "relationship_rationale": normalized_rationale,
        "confidence": str(confidence),
        "replace_current": str(replace_current).lower(),
    }
    normalized_scope = relevance_scope.strip()
    if normalized_scope:
        form["relevance_scope"] = normalized_scope
    return form


def render_source_upload(
    client: ResearchFlowApiClient,
    *,
    project_id: str,
    primary_question: dict[str, object],
) -> None:
    """Show current immutable paper versions and one explicit upload command."""
    sources = client.list_sources(project_id)
    current = next(
        (
            item
            for item in sources
            if item["relationship"]["review_status"] == "accepted"
            and item["relationship"]["lifecycle_status"] == "current"
        ),
        None,
    )

    st.subheader("Primary Paper")
    st.caption("The paper is evidence. Its role is explicit relative to the current question.")
    st.info("Internal MVP: upload only synthetic or specifically approved development fixtures.")

    if current is not None:
        source = current["source"]
        relation = current["relationship"]
        st.markdown(f"**{source['title']}**")
        st.caption(
            f"Version {relation['version']} · {source['original_filename']} · "
            f"SHA-256 `{source['content_hash']}`"
        )

    uploaded = st.file_uploader("PDF artifact", type=["pdf"], accept_multiple_files=False)
    default_title = Path(uploaded.name).stem if uploaded is not None else ""
    with st.form("primary-paper-upload", clear_on_submit=False):
        title = st.text_input("Paper title (required)", value=default_title, max_chars=500)
        authors = st.text_area("Authors (one per line)", max_chars=10_000)
        identifiers = st.text_area(
            "Identifiers (JSON)",
            value="{}",
            help='For example: {"doi": "10.xxxx/example"}',
        )
        rationale = st.text_area(
            "Why this is the Primary Paper (required)",
            max_chars=4_000,
        )
        relevance_scope = st.text_area("Relevance scope (optional)", max_chars=4_000)
        confidence = st.slider("Relationship confidence", 0.0, 1.0, 1.0, 0.05)
        replace_current = st.checkbox(
            "Replace the current Primary Paper with a new immutable version",
            disabled=current is None,
        )
        submitted = st.form_submit_button("Store Primary Paper", type="primary")

    if submitted:
        if uploaded is None:
            st.error("Select a PDF before submitting.")
            return
        try:
            form = primary_paper_upload_form(
                research_question_id=primary_question["id"],
                filename=uploaded.name,
                title=title,
                authors=authors,
                identifiers_json=identifiers,
                relationship_rationale=rationale,
                relevance_scope=relevance_scope,
                confidence=confidence,
                replace_current=replace_current,
            )
        except ValueError as error:
            st.error(str(error))
            return
        client.upload_primary_paper(
            project_id,
            file=uploaded,
            filename=uploaded.name,
            content_type=uploaded.type or "application/octet-stream",
            form=form,
            idempotency_key=str(uuid4()),
        )
        st.success("Primary Paper stored with immutable provenance.")
        st.rerun()

    historical = [item for item in sources if item is not current]
    if historical:
        with st.expander(f"Historical paper versions ({len(historical)})"):
            for item in historical:
                source = item["source"]
                relation = item["relationship"]
                st.caption(
                    f"v{relation['version']} · {source['title']} · "
                    f"{relation['lifecycle_status']} · {source['content_hash']}"
                )

    if current is not None:
        processing_run = render_source_processing(
            client,
            project_id=project_id,
            source=current["source"],
            relationship=current["relationship"],
        )
        render_extraction_execution(
            client,
            project_id=project_id,
            processing_run=processing_run,
        )
        render_repository_snapshot(
            client,
            project_id=project_id,
            primary_question=primary_question,
            source=current["source"],
            paper_relationship=current["relationship"],
        )
