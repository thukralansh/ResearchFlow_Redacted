"""Combined implementation-step and VerificationCheck inspection surface."""

import os
import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from apps.streamlit.api_client import ApiClientError, ResearchFlowApiClient  # noqa: E402
from apps.streamlit.components.implementation_step import (  # noqa: E402
    render_implementation_step,
)
from apps.streamlit.components.verification_check import (  # noqa: E402
    render_verification_check,
)

API_URL = os.getenv("RESEARCHFLOW_API_URL", "http://127.0.0.1:8000")

st.set_page_config(page_title="ResearchFlow · Verification", page_icon="RF", layout="wide")
st.title("Implementation and verification")
st.caption(
    "This page projects persisted application state. Generation, revisions, provenance, "
    "staleness, and result authority remain server-owned."
)

project_id = st.session_state.get("selected_project_id")
if not isinstance(project_id, str):
    st.info("Select a project from the ResearchFlow home page.")
    st.stop()

client = ResearchFlowApiClient(API_URL)
try:
    blueprints = client.list_blueprints(project_id)
    eligible = [
        item
        for item in blueprints
        if item["blueprint"]["status"] in {"provisional", "review_ready", "accepted"}
    ]
    if not eligible:
        st.info("No current eligible Blueprint is available.")
        st.stop()
    labels = {
        str(item["blueprint"]["id"]): (
            f"v{item['blueprint']['version']} · {item['blueprint']['status']}"
        )
        for item in eligible
    }
    blueprint_id = st.selectbox("Blueprint", list(labels), format_func=labels.__getitem__)
    try:
        plan = client.get_implementation_plan(project_id, blueprint_id)
    except ApiClientError as error:
        if error.status_code != 404:
            raise
        st.info("No implementation and verification plan exists for this Blueprint.")
        with st.form(f"generate-plan-{blueprint_id}"):
            workflow_id = st.text_input(
                "WorkflowRun ID",
                help="Temporary service-level trigger input;  orchestration will own launch flow.",
            )
            generate = st.form_submit_button("Generate ordered plan")
        if generate:
            client.generate_implementation_plan(
                project_id, blueprint_id, {"workflow_run_id": workflow_id.strip()}
            )
            st.rerun()
        st.stop()

    checks_by_step: dict[str, list[dict[str, object]]] = {}
    for check in plan["checks"]:
        checks_by_step.setdefault(str(check["implementation_step_id"]), []).append(check)
    for step in plan["steps"]:
        render_implementation_step(client, project_id=project_id, step=step)
        for check in checks_by_step.get(str(step["id"]), []):
            render_verification_check(client, project_id=project_id, check=check)
        st.divider()

    with st.expander("Exact provenance and validation dependencies"):
        for dependency in plan["dependencies"]:
            st.code(
                f"{dependency['artifact_type']}:{dependency['artifact_id']} -> "
                f"{dependency['dependency_role']} {dependency['dependency_type']}:"
                f"{dependency['dependency_id']} ({dependency['criticality']})"
            )
except ApiClientError as error:
    st.error(str(error))
