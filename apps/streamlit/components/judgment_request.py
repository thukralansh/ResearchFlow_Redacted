"""Read-only Judgment Request inspection;  owns resolution authority."""

from collections.abc import Mapping
from typing import Any

import streamlit as st
from apps.streamlit.api_client import ResearchFlowApiClient
from apps.streamlit.components.judgment_form import render_judgment_form
from apps.streamlit.components.judgment_history import render_judgment_history


def render_judgment_requests(client: ResearchFlowApiClient, *, project_id: str) -> None:
    summaries = client.list_judgment_requests(project_id)
    if not summaries:
        st.info("No Judgment Request has been created for this project.")
        return
    labels = {
        str(item["request"]["id"]): (
            f"{item['highest_criticality'].title()} · {item['request']['title']} · "
            f"{item['option_count']} option(s)"
        )
        for item in summaries
    }
    selected = st.selectbox(
        "Judgment Request",
        tuple(labels),
        format_func=labels.__getitem__,
        key=f"judgment-request:{project_id}:selected",
    )
    detail = client.get_judgment_request(project_id, selected)
    _render_detail(detail)
    render_judgment_form(client, project_id=project_id, detail=dict(detail))
    render_judgment_history(client, project_id=project_id, request_id=selected)


def _render_detail(detail: Mapping[str, Any]) -> None:
    request = detail["request"]
    ambiguity = detail["ambiguity"]
    st.header(str(request["title"]))
    st.warning(
        "This is an unresolved decision surface, not a Human Judgment. No option or AI "
        "recommendation is canonical until the separate human-decision workflow runs."
    )
    st.markdown(f"### Decision question\n{request['decision_question']}")
    st.markdown(f"### Exact Ambiguity\n{ambiguity['statement']}")
    st.caption(
        f"Ambiguity v{ambiguity['version']} · {ambiguity['review_status']}/"
        f"{ambiguity['lifecycle_status']} · request {request['status']}"
    )
    with st.expander("Why human authority is required", expanded=True):
        st.write(str(request["why_human_required"]))

    st.subheader("Options and implementation consequences")
    for option in detail["options"]:
        with st.container(border=True):
            st.markdown(f"**{option['rank']}. {option['title']}**")
            st.write(str(option["description"]))
            st.markdown("**Consequences**")
            st.write(str(option["implementation_consequences"]))
            with st.expander("Exact option evidence", expanded=False):
                st.markdown("**Supporting / qualifying**")
                st.json(option["supporting_evidence"], expanded=False)
                st.markdown("**Conflicting**")
                st.json(option["conflicting_evidence"], expanded=False)

    recommendation = detail.get("recommendation")
    st.subheader("AI recommendation (optional and non-authoritative)")
    if isinstance(recommendation, Mapping):
        st.info(str(recommendation["rationale"]))
        st.caption("AI-authored · non-authoritative · Human Judgment remains required")
    else:
        st.write(str(detail.get("no_recommendation_reason") or "No recommendation justified."))

    with st.expander("Affected objects and dependency-scoped criticality", expanded=True):
        for dependency in detail["dependencies"]:
            st.write(
                f"**{str(dependency['criticality']).title()}** · "
                f"{dependency['dependency_type']} `{dependency['dependency_id']}` · "
                f"{dependency['dependency_role']}"
            )
    with st.expander("Accepted/current and historical evidence", expanded=False):
        st.json(detail["evidence"], expanded=False)
