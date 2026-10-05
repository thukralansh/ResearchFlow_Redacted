"""Deterministic Blueprint eligibility inspection page."""

import os
import sys
from pathlib import Path
from uuid import UUID, uuid4

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from apps.streamlit.api_client import ApiClientError, ResearchFlowApiClient  # noqa: E402
from apps.streamlit.components.eligibility_item import render_eligibility_item  # noqa: E402

API_URL = os.getenv("RESEARCHFLOW_API_URL", "http://127.0.0.1:8000")

st.set_page_config(page_title="ResearchFlow · Blueprint eligibility", page_icon="RF", layout="wide")
st.title("Blueprint eligibility")
st.caption(
    "A deterministic gate over exact current project state. This page never generates a Blueprint."
)

project_id = st.session_state.get("selected_project_id")
if not isinstance(project_id, str):
    st.info("Select a project from the ResearchFlow home page.")
    st.stop()

client = ResearchFlowApiClient(API_URL)
try:
    assessments = client.list_blueprint_eligibility(project_id)
    if assessments:
        labels = {
            str(item["id"]): f"{item['status'].replace('_', ' ').title()} · {str(item['id'])[:8]}"
            for item in assessments
        }
        selected = st.selectbox("Assessment", list(labels), format_func=labels.__getitem__)
        detail = client.get_blueprint_eligibility(project_id, selected)
        assessment = detail["assessment"]
        st.metric("Status", str(assessment["status"]).replace("_", " ").title())
        st.caption(
            f"Rule {assessment['eligibility_rule_version']} · input {assessment['input_fingerprint']}"
        )
        for item in detail["items"]:
            with st.container(border=True):
                render_eligibility_item(item)
    else:
        st.info("No Blueprint eligibility assessment has been recorded.")

    with st.expander("Run deterministic assessment"):
        st.warning(
            "Use exact IDs from the active workflow.  will own this transition automatically."
        )
        with st.form("assess-blueprint-eligibility"):
            workflow_id = st.text_input("WorkflowRun ID")
            mapping_gate_id = st.text_input("Mapping review gate OperationEvent ID")
            submitted = st.form_submit_button("Assess eligibility", type="primary")
        if submitted:
            client.assess_blueprint_eligibility(
                project_id,
                {
                    "workflow_run_id": str(UUID(workflow_id)),
                    "mapping_review_gate_event_id": str(UUID(mapping_gate_id)),
                    "command_id": str(uuid4()),
                },
            )
            st.rerun()
except (ApiClientError, ValueError) as error:
    st.error(str(error))
