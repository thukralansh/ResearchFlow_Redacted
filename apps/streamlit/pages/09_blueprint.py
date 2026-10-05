"""Human Blueprint inspection and typed review commands."""

import os
import sys
from pathlib import Path
from uuid import uuid4

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from apps.streamlit.api_client import ApiClientError, ResearchFlowApiClient  # noqa: E402
from apps.streamlit.components.blueprint_history import (  # noqa: E402
    changed_section_types,
    render_blueprint_history,
)
from apps.streamlit.components.blueprint_section import render_blueprint_section  # noqa: E402

API_URL = os.getenv("RESEARCHFLOW_API_URL", "http://127.0.0.1:8000")

st.set_page_config(page_title="ResearchFlow · Blueprint", page_icon="RF", layout="wide")
st.title("Blueprint review")
st.caption(
    "Inspect immutable, provenance-bound versions. Review commands are executed by the "
    "application service; this page does not own scientific or lifecycle state."
)

project_id = st.session_state.get("selected_project_id")
if not isinstance(project_id, str):
    st.info("Select a project from the ResearchFlow home page.")
    st.stop()

client = ResearchFlowApiClient(API_URL)
try:
    history = client.list_blueprints(project_id)
    if not history:
        st.info("No Blueprint has been generated for this project.")
        st.stop()
    labels = {
        str(item["blueprint"]["id"]): (
            f"v{item['blueprint']['version']} · {item['blueprint']['status'].replace('_', ' ')}"
        )
        for item in history
    }
    selected_id = st.selectbox("Blueprint version", list(labels), format_func=labels.__getitem__)
    detail = client.get_blueprint(project_id, selected_id)
    blueprint = detail["blueprint"]
    st.metric("Status", str(blueprint["status"]).replace("_", " ").title())
    if blueprint["eligibility_status"] == "eligible_with_warnings":
        st.warning("This provisional Blueprint remains inspectable but cannot be accepted.")

    for section in detail["sections"]:
        render_blueprint_section(section)

    with st.expander("Exact provenance dependencies"):
        for dependency in detail["dependencies"]:
            st.code(
                f"{dependency['dependency_role']} · {dependency['criticality']} · "
                f"{dependency['dependency_type']}:{dependency['dependency_id']}"
            )

    if blueprint["status"] == "review_ready":
        with st.form(f"accept-blueprint-{selected_id}"):
            acceptance_rationale = st.text_area("Acceptance rationale", max_chars=4_000)
            accept = st.form_submit_button("Accept this Blueprint", type="primary")
        if accept:
            client.accept_blueprint(
                project_id,
                selected_id,
                {
                    "command_id": str(uuid4()),
                    "expected_version": blueprint["version"],
                    "expected_status": blueprint["status"],
                    "rationale": acceptance_rationale,
                },
            )
            st.rerun()

    if blueprint["status"] in {"draft", "provisional", "review_ready", "accepted"}:
        with st.form(f"revise-blueprint-{selected_id}"):
            feedback = st.text_area("Semantic revision feedback", max_chars=4_000)
            revision_rationale = st.text_area("Revision rationale", max_chars=4_000)
            replacement_assessment = st.text_input(
                "Replacement eligibility assessment ID (optional)",
                help="Use an exact newer assessment after resolving a warning.",
            )
            revise = st.form_submit_button("Request immutable revision")
        if revise:
            payload = {
                "command_id": str(uuid4()),
                "expected_version": blueprint["version"],
                "expected_status": blueprint["status"],
                "rationale": revision_rationale,
                "feedback": feedback,
                "eligibility_assessment_id": replacement_assessment.strip() or None,
            }
            client.revise_blueprint(project_id, selected_id, payload)
            st.rerun()

    render_blueprint_history(history, detail["history"])
    comparison_ids = [
        str(item["blueprint"]["id"])
        for item in history
        if str(item["blueprint"]["id"]) != selected_id
    ]
    if comparison_ids:
        with st.expander("Compare immutable versions"):
            comparison_id = st.selectbox("Compare with", comparison_ids)
            comparison = client.get_blueprint(project_id, comparison_id)
            changed = changed_section_types(detail, comparison)
            if changed:
                st.write("Changed sections: " + ", ".join(changed))
            else:
                st.info("The persisted section content is identical.")
except ApiClientError as error:
    st.error(str(error))
