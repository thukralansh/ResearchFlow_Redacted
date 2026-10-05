"""Immutable Human Judgment history and downstream reconciliation state."""

from typing import Any

import streamlit as st
from apps.streamlit.api_client import ResearchFlowApiClient


def render_judgment_history(
    client: ResearchFlowApiClient, *, project_id: str, request_id: str
) -> None:
    items = client.list_human_judgments(project_id, request_id)
    if not items:
        return
    st.subheader("Immutable Human Judgment history")
    for item in reversed(items):
        judgment: dict[str, Any] = item["judgment"]
        label = (
            f"v{judgment['version']} · {judgment['lifecycle_status']} · "
            f"reassessment {item['reassessment_status']}"
        )
        with st.expander(label, expanded=judgment["lifecycle_status"] == "current"):
            st.write(judgment["custom_decision"] or judgment["selected_option_id"])
            st.markdown("**Human rationale**")
            st.write(judgment["rationale"])
            st.caption(f"Confidence {judgment['confidence']} · ID {judgment['id']}")
            if item.get("error"):
                st.error(f"Downstream reassessment is retryable: {item['error']}")
            st.json(
                {
                    "dependencies": item["dependencies"],
                    "method_model_id": item["method_model_id"],
                    "method_model_assessment_id": item["method_model_assessment_id"],
                    "mental_model_id": item["mental_model_id"],
                    "mental_model_assessment_id": item["mental_model_assessment_id"],
                },
                expanded=False,
            )
