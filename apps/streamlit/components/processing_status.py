"""Visible Primary PDF processing controls and provenance status."""

import streamlit as st
from apps.streamlit.api_client import ResearchFlowApiClient


def render_source_processing(
    client: ResearchFlowApiClient,
    *,
    project_id: str,
    source: dict[str, object],
    relationship: dict[str, object],
) -> dict[str, object] | None:
    source_id = str(source["id"])
    runs = client.list_source_processing_runs(project_id, source_id)

    st.divider()
    st.subheader("Process Primary PDF")
    st.caption(
        "Parsing produces immutable, page-local provenance chunks. "
        "It does not perform knowledge extraction."
    )
    if st.button("Parse and chunk PDF", type="primary", disabled=bool(runs)):
        client.process_source(
            project_id,
            source_id,
            {
                "research_question_id": relationship["research_question_id"],
                "question_source_relationship_id": relationship["id"],
            },
        )
        st.rerun()

    if not runs:
        st.caption("Not yet processed.")
        return None

    run_view = runs[0]
    run = run_view["run"]
    chunks = run_view["chunks"]
    status = str(run["status"])
    if status == "succeeded":
        st.success(f"Processing succeeded · {len(chunks)} provenance chunks")
    elif status == "failed":
        st.error(str(run_view.get("message") or "PDF processing failed."))
    else:
        st.info("PDF processing is running.")
    st.caption(
        f"{run['parser_name']} {run['parser_version']} · "
        f"{run['normalization_version']} · {run['chunker_version']}"
    )
    if chunks:
        pages = sorted({int(chunk["page_number"]) for chunk in chunks})
        st.caption(f"Pages represented: {pages[0]}-{pages[-1]}")
        with st.expander("Inspect provenance chunks"):
            for chunk in chunks:
                section = chunk.get("subsection") or chunk.get("section") or "Unsectioned"
                st.markdown(
                    f"**Chunk {chunk['chunk_index']} · page {chunk['page_number']} · {section}**"
                )
                st.caption(
                    f"Offsets [{chunk['start_offset']}, {chunk['end_offset']}) · "
                    f"SHA-256 `{chunk['content_hash']}`"
                )
                st.text(str(chunk["chunk_text"]))
    return run_view
