"""Minimal extraction-only execution control for the pre- boundary."""

from __future__ import annotations

from uuid import uuid4

import streamlit as st
from apps.streamlit.api_client import ResearchFlowApiClient


def extraction_execution_payload(
    *,
    source_processing_run_id: object,
    workflow_run_id: str | None,
    continuation_cursor: str | None,
    explicit_retry: bool,
) -> dict[str, object]:
    """Build one bounded invocation; this cannot launch downstream stages."""
    payload: dict[str, object] = {
        "source_processing_run_id": str(source_processing_run_id),
        "max_batches": 1,
        "explicit_retry": explicit_retry,
    }
    if workflow_run_id is not None:
        payload["workflow_run_id"] = workflow_run_id
    if continuation_cursor is not None and not explicit_retry:
        payload["continuation_cursor"] = continuation_cursor
    return payload


def render_extraction_execution(
    client: ResearchFlowApiClient,
    *,
    project_id: str,
    processing_run: dict[str, object] | None,
) -> None:
    """Execute or continue proposition extraction one bounded batch at a time."""
    if processing_run is None:
        return
    raw_run = processing_run.get("run")
    if not isinstance(raw_run, dict) or raw_run.get("status") != "succeeded":
        return
    run_id = str(raw_run["id"])
    state_key = f"extraction_execution:{project_id}:{run_id}"
    pending_key = f"{state_key}:pending_idempotency_key"
    state = st.session_state.get(state_key)
    current = state if isinstance(state, dict) else None

    st.divider()
    st.subheader("Extract candidate knowledge")
    st.caption(
        "This temporary pre- control runs proposition extraction only. "
        "It never accepts candidates or starts downstream workflow stages."
    )
    if current is not None:
        workflow = current["workflow"]
        extraction = current["extraction"]
        st.caption(
            f"Workflow `{workflow['id']}` · {workflow['status']} · "
            f"{extraction['eligible_chunk_count']} eligible chunks"
        )
        if extraction["complete"]:
            st.success("Candidate extraction completed. Review remains a separate human action.")
            return

    label = "Start candidate extraction"
    explicit_retry = False
    workflow_run_id = None
    continuation_cursor = None
    if current is not None:
        workflow_run_id = str(current["workflow"]["id"])
        if current["workflow"]["status"] == "failed":
            label = "Retry failed extraction"
            explicit_retry = True
        else:
            label = "Continue candidate extraction"
            continuation_cursor = current["extraction"]["continuation_cursor"]

    if st.button(label, type="primary"):
        idempotency_key = st.session_state.get(pending_key)
        if not isinstance(idempotency_key, str):
            idempotency_key = str(uuid4())
            st.session_state[pending_key] = idempotency_key
        result = client.execute_extraction(
            project_id,
            extraction_execution_payload(
                source_processing_run_id=run_id,
                workflow_run_id=workflow_run_id,
                continuation_cursor=continuation_cursor,
                explicit_retry=explicit_retry,
            ),
            idempotency_key=idempotency_key,
        )
        st.session_state[state_key] = result
        st.session_state.pop(pending_key, None)
        st.rerun()
