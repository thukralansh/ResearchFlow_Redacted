"""Read-only structured Mental Model workspace."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import streamlit as st
from apps.streamlit.api_client import ResearchFlowApiClient
from apps.streamlit.components.freshness_badge import render_freshness_badge
from apps.streamlit.components.mental_model_graph import render_mental_model_graph
from apps.streamlit.components.provenance_drawer import render_provenance_drawer
from apps.streamlit.components.stale_reason import render_stale_reasons
from apps.streamlit.mental_model_view import group_members_by_role, latest_rendering, member_title


def _label(value: object) -> str:
    return str(value).replace("_", " ").title()


def render_mental_model_workspace(client: ResearchFlowApiClient, *, project_id: str) -> None:
    history = client.list_mental_models(project_id)
    current = client.get_current_mental_model(project_id)
    if not history:
        st.info("No Mental Model has been composed for the completed review state.")
        _render_generation_controls(client, project_id=project_id)
        return

    labels = {
        str(item["model"]["id"]): (
            f"v{item['model']['version']} · {_label(item['model']['status'])} · "
            f"{item['model']['title']}"
        )
        for item in history
    }
    current_id = str(current["model"]["id"]) if current is not None else None
    selected_id = st.selectbox(
        "Mental Model version",
        tuple(labels),
        index=tuple(labels).index(current_id) if current_id in labels else 0,
        format_func=labels.__getitem__,
        key=f"mental-model:{project_id}:selected",
    )
    detail = (
        current if current_id == selected_id else client.get_mental_model(project_id, selected_id)
    )
    if detail is None:
        st.error("The current Mental Model is no longer available. Refresh this page.")
        return
    _render_assessment_coverage(client, project_id=project_id)
    _render_detail(client, project_id=project_id, detail=detail)


def _render_assessment_coverage(client: ResearchFlowApiClient, *, project_id: str) -> None:
    coverage = client.get_current_mental_model_assessment_coverage(project_id)
    assessment = coverage.get("assessment")
    with st.container(border=True):
        st.markdown("#### Post-mapping assessment coverage")
        if bool(coverage["covered"]):
            st.success(str(coverage["reason"]))
        else:
            st.warning(str(coverage["reason"]))
        if isinstance(assessment, Mapping):
            repository = assessment.get("repository_snapshot_id") or "not applicable"
            st.caption(
                f"Assessment {assessment['id']} · {assessment['result']} · "
                f"MethodModel {assessment['method_model_id']} · repository {repository}"
            )
            with st.expander("Assessment rationale", expanded=False):
                st.write(str(assessment["rationale"]))


def _render_generation_controls(client: ResearchFlowApiClient, *, project_id: str) -> None:
    st.caption("Composition is available after the complete scientific review gate succeeds.")
    with st.form(f"mental-model:{project_id}:generate"):
        workflow_run_id = st.text_input("Workflow run ID")
        method_model_id = st.text_input("Accepted MethodModel ID")
        submitted = st.form_submit_button("Compose initial Mental Model", type="primary")
    if submitted:
        client.generate_mental_model(
            project_id,
            {"workflow_run_id": workflow_run_id, "method_model_id": method_model_id},
            request_id=str(uuid4()),
        )
        st.rerun()


def _render_detail(
    client: ResearchFlowApiClient, *, project_id: str, detail: Mapping[str, Any]
) -> None:
    model = detail["model"]
    st.header(str(model["title"]))
    render_freshness_badge(model, is_current=bool(detail["is_current"]))
    render_stale_reasons(
        client.get_artifact_freshness(project_id, "mental_model", str(model["id"]))
    )
    st.caption(
        f"Immutable version {model['version']} · fingerprint {model['composition_fingerprint']} · "
        f"confidence {model.get('confidence') if model.get('confidence') is not None else 'not set'}"
    )
    st.info(
        "This structure contains accepted/current project knowledge at composition time. "
        "Rendered prose and graph layout are views, not canonical knowledge."
    )
    st.markdown(f"### Research Question\n{detail['question']['question_text']}")
    if detail["question"].get("scope_text"):
        st.caption(str(detail["question"]["scope_text"]))

    method = detail["method_model"]
    with st.expander(
        f"Accepted MethodModel v{method['version']} · {method['name']}", expanded=False
    ):
        st.write(str(method["objective"]))
        st.caption(
            f"{method['review_status']}/{method['lifecycle_status']} · "
            f"{method['freshness_status']} · {len(detail['method_elements'])} elements"
        )
        for item in detail["method_elements"]:
            element = item["member"]["element"]
            with st.expander(
                f"{_label(item['member']['review_section'])} · "
                f"{_label(element['element_type'])} v{element['version']}",
                expanded=False,
            ):
                st.json(item["member"]["subtype_payload"], expanded=False)
                render_provenance_drawer(item["evidence"], key=f"method:{item['member']['id']}")

    st.subheader("Structured composition")
    for role, members in group_members_by_role(detail["members"]):
        st.markdown(f"#### {_label(role)} · {len(members)}")
        for item in members:
            member = item["member"]
            with st.container(border=True):
                st.markdown(f"**{member_title(item)}**")
                st.caption(
                    f"{_label(item['target_type'])} · member {str(member['id'])[:12]}… · "
                    f"confidence {member.get('confidence') if member.get('confidence') is not None else 'not set'}"
                )
                st.write(str(member["inclusion_rationale"]))
                if item["target_type"] == "knowledge_object":
                    target = item["target"]
                    st.caption(
                        f"Underlying canonical object v{target['version']} · "
                        f"{target['review_status']}/{target['lifecycle_status']} · "
                        f"{target['grounding_status']}"
                    )
                render_provenance_drawer(item["evidence"], key=f"member:{member['id']}")

    with st.expander("Static relationship graph", expanded=False):
        render_mental_model_graph(detail)

    st.subheader("Narrative rendering (noncanonical view)")
    rendering = latest_rendering(detail["renderings"])
    if rendering is None:
        st.warning("This model has no narrative rendering.")
    else:
        st.markdown(str(rendering["content"]))
        st.caption(
            f"View {rendering['id']} · prompt {rendering.get('prompt_version') or 'unknown'} · "
            f"{rendering.get('provider') or 'unknown'}/{rendering.get('model_version') or 'unknown'}"
        )
    if detail["is_current"]:
        with st.form(f"mental-model:{model['id']}:render"):
            workflow_run_id = st.text_input("Workflow run ID for rendering audit")
            refresh = st.form_submit_button("Generate a new narrative view")
        if refresh:
            client.refresh_mental_model_rendering(
                project_id,
                str(model["id"]),
                {"workflow_run_id": workflow_run_id, "explicit_rerun": True},
                request_id=str(uuid4()),
            )
            st.rerun()
