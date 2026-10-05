"""Dedicated Primary Paper view for the internal Streamlit workspace."""

import os
import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from apps.streamlit.api_client import ApiClientError, ResearchFlowApiClient  # noqa: E402
from apps.streamlit.components.source_upload import render_source_upload  # noqa: E402

API_URL = os.getenv("RESEARCHFLOW_API_URL", "http://127.0.0.1:8000")

st.set_page_config(page_title="ResearchFlow · Sources", page_icon="RF", layout="wide")
st.title("Evidence source")

project_id = st.session_state.get("selected_project_id")
if not isinstance(project_id, str):
    st.info("Open or create a project from the ResearchFlow home page first.")
else:
    client = ResearchFlowApiClient(API_URL)
    try:
        project = client.get_project(project_id)
        render_source_upload(
            client,
            project_id=project_id,
            primary_question=project["primary_question"],
        )
    except ApiClientError as error:
        st.error(str(error))
