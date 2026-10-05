"""Dedicated Repository Stage A coverage page for the internal workspace."""

import os
import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from apps.streamlit.api_client import ApiClientError, ResearchFlowApiClient  # noqa: E402
from apps.streamlit.components.repository_coverage import (  # noqa: E402
    render_repository_coverage,
)
from apps.streamlit.components.repository_inspection import (  # noqa: E402
    render_repository_inspection,
)

API_URL = os.getenv("RESEARCHFLOW_API_URL", "http://127.0.0.1:8000")

st.set_page_config(page_title="ResearchFlow · Repository", page_icon="RF", layout="wide")
st.title("Repository evidence coverage")

project_id = st.session_state.get("selected_project_id")
if not isinstance(project_id, str):
    st.info("Open or create a project from the ResearchFlow home page first.")
else:
    client = ResearchFlowApiClient(API_URL)
    try:
        repository_history = client.list_repository_history(project_id)
        current = next(
            (
                item
                for item in reversed(repository_history)
                if item["relationship"]["review_status"] == "accepted"
                and item["relationship"]["lifecycle_status"] == "current"
            ),
            None,
        )
        if current is None:
            st.info("Attach and confirm a public repository from the Sources page first.")
            render_repository_inspection(
                client,
                project_id=project_id,
                repository_view=None,
            )
        else:
            render_repository_coverage(
                client,
                project_id=project_id,
                repository_view=current,
            )
    except ApiClientError as error:
        st.error(str(error))
