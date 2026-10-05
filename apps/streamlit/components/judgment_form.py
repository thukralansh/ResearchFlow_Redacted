"""Strongly confirmed Human Judgment form over the channel-neutral API."""

from decimal import Decimal
from typing import Any
from uuid import uuid4

import streamlit as st
from apps.streamlit.api_client import ResearchFlowApiClient


def render_judgment_form(
    client: ResearchFlowApiClient, *, project_id: str, detail: dict[str, Any]
) -> None:
    request = detail["request"]
    if request["status"] != "open":
        st.success("This Judgment Request is resolved.")
        return
    options = {str(item["id"]): str(item["title"]) for item in detail["options"]}
    custom = st.checkbox("Use a custom human decision", key=f"judgment-custom:{request['id']}")
    selected = (
        None
        if custom
        else st.selectbox("Decision", tuple(options), format_func=options.__getitem__)
    )
    custom_text = st.text_area("Custom decision") if custom else None
    rationale = st.text_area("Human rationale (required)")
    confidence = st.slider("Confidence", 0.0, 1.0, 0.7, 0.05)
    method_affected = st.checkbox(
        "This decision affects MethodModel interpretation or implementation logic"
    )
    impact = st.text_area("Method-impact rationale (required)")
    gate = st.text_input("Mapping-review gate Event ID")
    applicable = st.checkbox("Repository evidence branch is applicable", value=True)
    confirmed = st.checkbox("I confirm this is my consequential Human Judgment")
    if st.button("Record immutable Human Judgment", disabled=not confirmed):
        result = client.resolve_judgment_request(
            project_id,
            str(request["id"]),
            {
                "workflow_run_id": request["workflow_run_id"],
                "command_id": str(uuid4()),
                "selected_option_id": selected,
                "custom_decision": custom_text,
                "rationale": rationale,
                "confidence": str(Decimal(str(confidence))),
                "method_model_affected": method_affected,
                "method_impact_rationale": impact,
                "mapping_review_gate_event_id": gate,
                "repository_applicable": applicable,
                "confirmed": confirmed,
            },
        )
        if result["reassessment_status"] == "succeeded":
            st.success("Human Judgment recorded and downstream models reconciled.")
        else:
            st.warning("Human Judgment is durable; downstream reassessment is retryable.")
        st.json(result)
