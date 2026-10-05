"""Evidence-first proposition review page for the internal MVP workspace."""

import os
import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from apps.streamlit.api_client import ApiClientError, ResearchFlowApiClient  # noqa: E402
from apps.streamlit.components.knowledge_review import (  # noqa: E402
    chunk_location_map,
    render_knowledge_review,
)

API_URL = os.getenv("RESEARCHFLOW_API_URL", "http://127.0.0.1:8000")

st.set_page_config(page_title="ResearchFlow · Knowledge review", page_icon="RF", layout="wide")
st.title("Candidate knowledge review")
st.caption(
    "Review question-relevant propositions and their exact evidence. Nothing becomes "
    "canonical until an explicit human command succeeds."
)

project_id = st.session_state.get("selected_project_id")
if not isinstance(project_id, str):
    st.info("Open or create a project from the ResearchFlow home page first.")
else:
    client = ResearchFlowApiClient(API_URL)
    try:
        render_knowledge_review(
            client,
            project_id=project_id,
            chunk_locations=chunk_location_map(client, project_id=project_id),
        )
    except ApiClientError as error:
        st.error(str(error))

st.page_link(
    "pages/05_method_review.py",
    label="Continue to structured MethodModel review",
    icon=":material/account_tree:",
)
