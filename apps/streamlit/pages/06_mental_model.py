"""Structured Mental Model inspection page for the internal MVP workspace."""

import os
import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from apps.streamlit.api_client import ApiClientError, ResearchFlowApiClient  # noqa: E402
from apps.streamlit.components.mental_model import (  # noqa: E402
    render_mental_model_workspace,
)

API_URL = os.getenv("RESEARCHFLOW_API_URL", "http://127.0.0.1:8000")

st.set_page_config(page_title="ResearchFlow · Mental Model", page_icon="RF", layout="wide")
st.title("Project Mental Model")
st.caption("Inspect accepted-only structured understanding and its exact provenance.")

project_id = st.session_state.get("selected_project_id")
if not isinstance(project_id, str):
    st.info("Open or create a project from the ResearchFlow home page first.")
else:
    try:
        render_mental_model_workspace(
            ResearchFlowApiClient(API_URL),
            project_id=project_id,
        )
    except ApiClientError as error:
        st.error(str(error))
