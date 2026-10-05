"""Thin Project and inquiry workspace for the internal MVP."""

import os
import sys
from pathlib import Path
from uuid import uuid4

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from apps.streamlit.api_client import ApiClientError, ResearchFlowApiClient  # noqa: E402
from apps.streamlit.components.source_upload import render_source_upload  # noqa: E402

API_URL = os.getenv("RESEARCHFLOW_API_URL", "http://127.0.0.1:8000")
_PROJECT_CREATION_MODE_KEY = "project_creation_mode"
_PROJECT_SELECTOR_KEY = "open_project_id"
_PROJECT_SELECTOR_PENDING_KEY = "pending_open_project_id"


def _client() -> ResearchFlowApiClient:
    return ResearchFlowApiClient(API_URL)


def _create_project(client: ResearchFlowApiClient) -> None:
    st.subheader("Begin with the question")
    st.write("Define the inquiry before attaching evidence. Papers and repositories come next.")
    with st.form("create-project", clear_on_submit=True):
        title = st.text_input("Project title", max_chars=200)
        question = st.text_area("Primary research question", max_chars=8_000)
        scope = st.text_area("Scope", max_chars=8_000)
        target = st.text_input("Target outcome", max_chars=2_000)
        depth = st.text_input(
            "Reproduction depth",
            value="functional reproduction",
            max_chars=200,
        )
        context = st.text_area("Context or constraints", max_chars=8_000)
        submitted = st.form_submit_button("Create project", type="primary")
    if submitted:
        result = client.create_project(
            {
                "title": title,
                "primary_question": question,
                "scope": scope or None,
                "target_outcome": target,
                "reproduction_depth": depth,
                "context_constraints": context or None,
            },
            idempotency_key=str(uuid4()),
        )
        project_id = str(result["project"]["id"])
        st.session_state["selected_project_id"] = project_id
        st.session_state[_PROJECT_SELECTOR_PENDING_KEY] = project_id
        st.session_state[_PROJECT_CREATION_MODE_KEY] = False
        st.rerun()


def _select_existing_project() -> None:
    selected = st.session_state.get(_PROJECT_SELECTOR_KEY)
    if isinstance(selected, str):
        st.session_state["selected_project_id"] = selected
        st.session_state[_PROJECT_CREATION_MODE_KEY] = False


def _begin_project_creation() -> None:
    st.session_state[_PROJECT_CREATION_MODE_KEY] = True


def _cancel_project_creation() -> None:
    st.session_state[_PROJECT_CREATION_MODE_KEY] = False


def _project_selector(
    projects: list[dict[str, object]],
) -> str | None:
    if not projects:
        st.sidebar.caption("No projects yet")
        return None
    labels = {str(project["id"]): str(project["title"]) for project in projects}
    project_ids = list(labels)
    pending = st.session_state.pop(_PROJECT_SELECTOR_PENDING_KEY, None)
    if pending in labels:
        st.session_state[_PROJECT_SELECTOR_KEY] = pending
    selected = st.session_state.get("selected_project_id")
    if selected not in labels:
        selected = project_ids[0]
    widget_selected = st.session_state.get(_PROJECT_SELECTOR_KEY)
    if widget_selected not in labels:
        st.session_state[_PROJECT_SELECTOR_KEY] = selected
    selected = st.sidebar.selectbox(
        "Open project",
        project_ids,
        format_func=labels.__getitem__,
        key=_PROJECT_SELECTOR_KEY,
        on_change=_select_existing_project,
    )
    st.session_state["selected_project_id"] = selected
    return selected


def _render_project(client: ResearchFlowApiClient, project_id: str) -> None:
    view = client.get_project(project_id)
    project = view["project"]
    primary = view["primary_question"]

    st.caption(f"PROJECT / {str(project['id'])[:8]}")
    st.header(project["title"])
    st.markdown(f"### {primary['question_text']}")
    if primary.get("scope_text"):
        st.write(primary["scope_text"])

    metric_columns = st.columns(3)
    metric_columns[0].metric("Inquiry", str(primary["inquiry_status"]).replace("_", " "))
    metric_columns[1].metric("Question version", primary["version"])
    metric_columns[2].metric("Reproduction", project["reproduction_depth"])

    with st.expander("Project configuration"):
        with st.form("update-project"):
            title = st.text_input("Project title", value=project["title"], max_chars=200)
            target = st.text_input(
                "Target outcome", value=project["target_outcome"], max_chars=2_000
            )
            depth = st.text_input(
                "Reproduction depth", value=project["reproduction_depth"], max_chars=200
            )
            context = st.text_area(
                "Context or constraints",
                value=project.get("context_constraints") or "",
                max_chars=8_000,
            )
            if st.form_submit_button("Save configuration"):
                client.update_project(
                    project_id,
                    {
                        "expected_lock_version": project["lock_version"],
                        "title": title,
                        "target_outcome": target,
                        "reproduction_depth": depth,
                        "context_constraints": context or None,
                    },
                )
                st.rerun()

    left, right = st.columns(2)
    with left:
        st.subheader("Refine the question")
        with st.form("refine-question"):
            refined_text = st.text_area(
                "Question text",
                value=primary["question_text"],
                max_chars=8_000,
            )
            refined_scope = st.text_area(
                "Scope",
                value=primary.get("scope_text") or "",
                max_chars=8_000,
            )
            if st.form_submit_button("Create new question version"):
                client.refine_question(
                    project_id,
                    str(primary["id"]),
                    {
                        "expected_version": primary["version"],
                        "expected_project_lock_version": project["lock_version"],
                        "question_text": refined_text,
                        "scope_text": refined_scope or None,
                    },
                )
                st.rerun()

    with right:
        st.subheader("Inquiry state")
        current_status = str(primary["inquiry_status"])
        if current_status == "answered":
            if st.button("Reopen inquiry"):
                client.change_status(
                    project_id,
                    str(primary["id"]),
                    {
                        "expected_version": primary["version"],
                        "expected_status": current_status,
                        "target_status": "open",
                    },
                    reopen=True,
                )
                st.rerun()
        else:
            target_status = st.selectbox(
                "Update progress",
                ["open", "partially_answered", "answered"],
                index=["open", "partially_answered", "answered"].index(current_status),
            )
            if st.button("Update inquiry status", disabled=target_status == current_status):
                client.change_status(
                    project_id,
                    str(primary["id"]),
                    {
                        "expected_version": primary["version"],
                        "expected_status": current_status,
                        "target_status": target_status,
                    },
                )
                st.rerun()

    st.subheader("Subquestions")
    subquestions = [
        question
        for question in view["current_questions"]
        if question["question_type"] == "subquestion"
    ]
    for question in subquestions:
        st.markdown(f"- **{question['question_text']}** · {question['inquiry_status']}")
    with st.form("create-subquestion", clear_on_submit=True):
        subquestion = st.text_input("Add a focused subquestion", max_chars=8_000)
        if st.form_submit_button("Add subquestion"):
            client.create_subquestion(
                project_id,
                {
                    "parent_question_id": primary["id"],
                    "question_text": subquestion,
                    "scope_text": None,
                    "priority": "medium",
                },
            )
            st.rerun()

    st.divider()
    render_source_upload(
        client,
        project_id=project_id,
        primary_question=primary,
    )
    st.page_link(
        "pages/04_knowledge_review.py",
        label="Review candidate knowledge",
        icon=":material/fact_check:",
    )
    st.page_link(
        "pages/05_method_review.py",
        label="Review structured scientific method",
        icon=":material/account_tree:",
    )
    st.page_link(
        "pages/06_mental_model.py",
        label="Inspect project Mental Model",
        icon=":material/hub:",
    )
    st.page_link(
        "pages/08_blueprint_eligibility.py",
        label="Assess Blueprint eligibility",
        icon=":material/checklist:",
    )
    st.page_link(
        "pages/09_blueprint.py",
        label="Review Blueprint",
        icon=":material/architecture:",
    )
    st.page_link(
        "pages/10_verification.py",
        label="Implementation and verification plan",
        icon=":material/rule:",
    )


def main() -> None:
    """Render Project state obtained only through the backend API."""
    st.set_page_config(page_title="ResearchFlow", page_icon="RF", layout="wide")
    st.markdown(
        """
        <style>
        :root { --rf-ink: #18312d; --rf-accent: #c65d35; --rf-paper: #f4f0e6; }
        .stApp { background: radial-gradient(circle at 85% 8%, #e8d8b9 0, transparent 24%), var(--rf-paper); }
        h1, h2, h3 { color: var(--rf-ink); letter-spacing: -0.025em; }
        [data-testid="stMetric"] { border-top: 3px solid var(--rf-accent); padding-top: .7rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.title("ResearchFlow")
    st.caption("Build durable understanding before implementation")

    client = _client()
    try:
        projects = client.list_projects()
        selected_project_id = _project_selector(projects)
        if selected_project_id is None:
            _create_project(client)
        elif bool(st.session_state.get(_PROJECT_CREATION_MODE_KEY, False)):
            st.sidebar.button("Cancel new project", on_click=_cancel_project_creation)
            _create_project(client)
        else:
            st.sidebar.button("New project", on_click=_begin_project_creation)
            _render_project(client, selected_project_id)
    except ApiClientError as error:
        st.error(str(error))
        if error.connection_failed:
            st.info(
                "Start the ResearchFlow API, then reload this page. No local project state was used."
            )


if __name__ == "__main__":
    main()
