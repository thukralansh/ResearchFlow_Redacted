"""Four-section, evidence-first MethodModel review workspace."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from functools import partial
from typing import Any, cast
from uuid import uuid4

import streamlit as st
from apps.streamlit.api_client import ApiClientError, ResearchFlowApiClient
from apps.streamlit.method_review_view import (
    METHOD_SECTION_LABELS,
    METHOD_SECTION_ORDER,
    element_title,
    group_method_members,
    paginate_method_section,
    section_expanded_by_default,
)


def _label(value: object) -> str:
    return str(value).replace("_", " ").title()


def _transition_actions(item: Mapping[str, Any]) -> tuple[str, ...]:
    return {
        ("candidate", "current"): ("accept", "reject", "request-revision"),
        ("needs_revision", "current"): ("reject",),
        ("accepted", "current"): ("challenge",),
        ("accepted", "challenged"): ("reaffirm",),
    }.get((item["review_status"], item["lifecycle_status"]), ())


def _base_command(item: Mapping[str, Any], rationale: str) -> dict[str, object]:
    return {
        "command_id": str(uuid4()),
        "expected_version": int(item["version"]),
        "expected_review_status": str(item["review_status"]),
        "expected_lifecycle_status": str(item["lifecycle_status"]),
        "rationale": rationale,
    }


def _event_references(event: Mapping[str, Any], identifiers: set[str]) -> bool:
    return any(
        str(reference.get("id")) in identifiers
        for collection in (event.get("input_refs", ()), event.get("output_refs", ()))
        for reference in collection
        if isinstance(reference, Mapping)
    )


def _section_state_key(project_id: str, model_id: str, section: str, suffix: str) -> str:
    return f"method-review:{project_id}:{model_id}:{section}:{suffix}"


def _execute(
    invoke: Callable[[], object],
    *,
    project_id: str,
    model_id: str,
    section: str,
) -> None:
    try:
        invoke()
    except ApiClientError as error:
        st.session_state[_section_state_key(project_id, model_id, section, "error")] = str(error)
        st.session_state[_section_state_key(project_id, model_id, section, "expanded")] = True
        st.rerun()
    st.session_state.pop(_section_state_key(project_id, model_id, section, "error"), None)
    st.success("Review state updated.")
    st.rerun()


def _render_evidence(
    client: ResearchFlowApiClient,
    *,
    project_id: str,
    model_id: str,
    member: Mapping[str, Any],
    chunk_locations: Mapping[str, Mapping[str, Any]],
    section: str,
) -> None:
    evidence = member.get("evidence", ())
    if not evidence:
        st.warning("No evidence is attached to this MethodElement.")
        return
    for link in evidence:
        location = chunk_locations.get(str(link["source_chunk_id"]))
        repository_component_id = link.get("repository_component_id")
        if repository_component_id is not None:
            location_text = f"RepositoryComponent {repository_component_id}"
        elif location is not None:
            location_text = (
                f"Page {location['page_number']} · {location.get('section') or 'Unsectioned'}"
            )
        else:
            location_text = "Exact SourceChunk location unavailable"
        st.caption(
            f"{_label(link['evidence_relation'])} · {location_text} · "
            f"{_label(link['review_status'])}/{_label(link['lifecycle_status'])}"
        )
        st.write(str(link["evidence_excerpt"]))
        actions = _transition_actions(link)
        if not actions:
            continue
        link_id = str(link["id"])
        with st.form(f"method-evidence:{model_id}:{link_id}"):
            action = st.selectbox("Evidence action", actions, format_func=_label)
            rationale = st.text_area("Evidence rationale", max_chars=4_000)
            submitted = st.form_submit_button("Review evidence")
        if submitted:
            payload = _base_command(link, rationale)
            _execute(
                partial(
                    client.review_method_evidence,
                    project_id,
                    model_id,
                    str(member["element"]["id"]),
                    link_id,
                    action,
                    payload,
                    request_id=str(uuid4()),
                ),
                project_id=project_id,
                model_id=model_id,
                section=section,
            )


def _render_element(
    client: ResearchFlowApiClient,
    *,
    project_id: str,
    model: Mapping[str, Any],
    member: Mapping[str, Any],
    chunk_locations: Mapping[str, Mapping[str, Any]],
    section: str,
    history: Sequence[Mapping[str, Any]],
) -> None:
    element = member["element"]
    model_id = str(model["id"])
    element_id = str(element["id"])
    title = element_title(member)
    with st.container(border=True):
        st.markdown(f"**{_label(element['element_type'])}: {title}**")
        st.caption(
            f"v{element['version']} · {_label(element['review_status'])}/"
            f"{_label(element['lifecycle_status'])} · {_label(element['grounding_status'])}"
        )
        payload = member["subtype_payload"]
        if element["element_type"] == "equation":
            st.code(str(payload.get("latex_expression", "")), language=None)
            if payload.get("explanation"):
                st.write(str(payload["explanation"]))
        else:
            st.json(payload, expanded=False)
        with st.expander("Evidence and immutable history", expanded=False):
            _render_evidence(
                client,
                project_id=project_id,
                model_id=model_id,
                member=member,
                chunk_locations=chunk_locations,
                section=section,
            )
            st.caption(
                f"Element lineage {element['lineage_id']} · immutable version {element['version']}"
            )
            identifiers = {element_id, *(str(item["id"]) for item in member.get("evidence", ()))}
            relevant_history = tuple(
                event for event in history if _event_references(event, identifiers)
            )
            if relevant_history:
                st.markdown("**Review history**")
                for event in relevant_history:
                    st.caption(
                        f"{_label(event['operation_type'])} · event {str(event['id'])[:12]}…"
                    )
                    if event.get("reason"):
                        st.write(str(event["reason"]))
            else:
                st.caption("No review mutation has been recorded for this immutable child.")

        actions = _transition_actions(element)
        if actions:
            with st.form(f"method-element-review:{model_id}:{element_id}"):
                action = st.selectbox("Element action", actions, format_func=_label)
                rationale = st.text_area("Decision rationale", max_chars=4_000)
                submitted = st.form_submit_button("Apply element decision")
            if submitted:
                command = _base_command(element, rationale)
                _execute(
                    lambda: client.review_method_element(
                        project_id,
                        model_id,
                        element_id,
                        action,
                        command,
                        request_id=str(uuid4()),
                    ),
                    project_id=project_id,
                    model_id=model_id,
                    section=section,
                )

        with st.expander("Edit or exclude", expanded=False):
            revision_payload = {
                "element_type": element["element_type"],
                **dict(payload),
            }
            with st.form(f"method-element-edit:{model_id}:{element_id}"):
                raw_payload = st.text_area(
                    "Typed semantic payload (JSON)",
                    value=json.dumps(revision_payload, indent=2, default=str),
                    max_chars=20_000,
                )
                epistemic = st.selectbox(
                    "Epistemic status",
                    (
                        "explicit",
                        "inferred",
                        "implementation_convention",
                        "repository_observed",
                        "human_decided",
                    ),
                    index=(
                        "explicit",
                        "inferred",
                        "implementation_convention",
                        "repository_observed",
                        "human_decided",
                    ).index(str(element["epistemic_status"])),
                )
                provide_confidence = st.checkbox(
                    "Provide confidence",
                    value=element.get("confidence") is not None,
                )
                confidence_value = st.number_input(
                    "Confidence",
                    min_value=0.0,
                    max_value=1.0,
                    value=float(element.get("confidence") or 0.0),
                    step=0.01,
                    disabled=not provide_confidence,
                )
                rationale = st.text_area("Revision rationale", max_chars=4_000)
                revise = st.form_submit_button("Create semantic revision")
            if revise:
                try:
                    parsed = json.loads(raw_payload)
                except json.JSONDecodeError:
                    st.session_state[_section_state_key(project_id, model_id, section, "error")] = (
                        "The semantic payload is not valid JSON."
                    )
                    st.session_state[
                        _section_state_key(project_id, model_id, section, "expanded")
                    ] = True
                    st.rerun()
                command = {
                    **_base_command(element, rationale),
                    "epistemic_status": epistemic,
                    "confidence": confidence_value if provide_confidence else None,
                    "payload": parsed,
                }
                _execute(
                    lambda: client.revise_method_element(
                        project_id,
                        model_id,
                        element_id,
                        command,
                        request_id=str(uuid4()),
                    ),
                    project_id=project_id,
                    model_id=model_id,
                    section=section,
                )
            with st.form(f"method-element-exclude:{model_id}:{element_id}"):
                rationale = st.text_area("Exclusion rationale", max_chars=4_000)
                exclude = st.form_submit_button("Exclude from a new model revision")
            if exclude:
                command = {
                    "command_id": str(uuid4()),
                    "expected_model_version": int(model["version"]),
                    "expected_composition_fingerprint": str(model["composition_fingerprint"]),
                    "rationale": rationale,
                }
                _execute(
                    lambda: client.exclude_method_element(
                        project_id,
                        model_id,
                        element_id,
                        command,
                        request_id=str(uuid4()),
                    ),
                    project_id=project_id,
                    model_id=model_id,
                    section=section,
                )


def _render_section(
    client: ResearchFlowApiClient,
    *,
    project_id: str,
    detail: Mapping[str, Any],
    section: str,
    members: Sequence[Mapping[str, Any]],
    chunk_locations: Mapping[str, Mapping[str, Any]],
) -> None:
    model = detail["model"]
    model_id = str(model["id"])
    error_key = _section_state_key(project_id, model_id, section, "error")
    expanded_key = _section_state_key(project_id, model_id, section, "expanded")
    error = st.session_state.get(error_key)
    expanded = bool(
        st.session_state.get(
            expanded_key,
            section_expanded_by_default(section, has_error=isinstance(error, str)),
        )
    )
    with st.expander(
        f"{METHOD_SECTION_LABELS[section]} · {len(members)} elements", expanded=expanded
    ):
        if isinstance(error, str):
            st.error(error)
        page_key = _section_state_key(project_id, model_id, section, "page")
        fingerprint_key = _section_state_key(project_id, model_id, section, "page-fingerprint")
        provisional = paginate_method_section(model_id, section, members, requested_page=1)
        if st.session_state.get(fingerprint_key) != provisional.result_fingerprint:
            st.session_state[fingerprint_key] = provisional.result_fingerprint
            st.session_state[page_key] = 1
        requested_page = int(st.session_state.get(page_key, 1))
        page = paginate_method_section(model_id, section, members, requested_page=requested_page)
        if page.page_count > 1:
            st.number_input(
                "Display page",
                min_value=1,
                max_value=page.page_count,
                key=page_key,
            )
        st.caption(
            f"Showing {len(page.items)} of {page.total_items} elements · page "
            f"{page.page_number} of {page.page_count}. Pagination is display-only."
        )
        for member in page.items:
            _render_element(
                client,
                project_id=project_id,
                model=model,
                member=member,
                chunk_locations=chunk_locations,
                section=section,
                history=detail["history"],
            )

        current_review = next(
            (item for item in detail["section_reviews"] if item["review_section"] == section),
            None,
        )
        if current_review is not None:
            st.success(
                f"Current section outcome: {_label(current_review['outcome'])} · "
                f"fingerprint {str(current_review['member_set_fingerprint'])[:12]}…"
            )
        st.caption(
            "Approval submits and validates the complete immutable section set, including "
            "elements not visible on this display page."
        )
        with st.form(f"method-section-review:{model_id}:{section}"):
            outcome = st.selectbox(
                "Section outcome", ("accepted", "not_applicable"), format_func=_label
            )
            rationale = st.text_area("Section rationale", max_chars=4_000)
            submitted = st.form_submit_button("Record complete-section review")
        if submitted:
            command = {
                "command_id": str(uuid4()),
                "displayed_member_ids": [str(item["method_element_id"]) for item in members],
                "displayed_member_set_fingerprint": detail["section_fingerprints"][section],
                "outcome": outcome,
                "rationale": rationale,
            }
            _execute(
                lambda: client.review_method_section(
                    project_id,
                    model_id,
                    section,
                    command,
                    request_id=str(uuid4()),
                ),
                project_id=project_id,
                model_id=model_id,
                section=section,
            )


def _render_aggregate(
    client: ResearchFlowApiClient,
    *,
    project_id: str,
    detail: Mapping[str, Any],
) -> None:
    model = detail["model"]
    assessment = detail["assessment"]
    st.subheader("Aggregate review")
    columns = st.columns(4)
    columns[0].metric("Assessment", _label(assessment["outcome"]))
    columns[1].metric("Unsupported required", assessment["unsupported_required_count"])
    columns[2].metric("Mixed evidence", assessment["mixed_evidence_count"])
    columns[3].metric("Sections resolved", len(detail["section_reviews"]))
    if detail.get("blocking_reason"):
        st.warning(str(detail["blocking_reason"]))
    st.caption(
        "Aggregate acceptance is a separate explicit command and never accepts child records."
    )
    if model["review_status"] != "accepted" or model["lifecycle_status"] != "current":
        with st.form(f"method-model-accept:{model['id']}"):
            rationale = st.text_area("Aggregate acceptance rationale", max_chars=4_000)
            submitted = st.form_submit_button("Accept complete MethodModel", type="primary")
        if submitted:
            command = {
                "command_id": str(uuid4()),
                "expected_version": int(model["version"]),
                "expected_review_status": str(model["review_status"]),
                "expected_lifecycle_status": str(model["lifecycle_status"]),
                "expected_composition_fingerprint": str(model["composition_fingerprint"]),
                "rationale": rationale,
            }
            _execute(
                lambda: client.accept_method_model(
                    project_id, str(model["id"]), command, request_id=str(uuid4())
                ),
                project_id=project_id,
                model_id=str(model["id"]),
                section="aggregate",
            )
    else:
        st.success("This exact MethodModel version is accepted/current.")


def _render_combined_gate(
    client: ResearchFlowApiClient,
    *,
    project_id: str,
    model_id: str,
) -> None:
    st.subheader("Primary-source scientific review gate")
    gate = client.get_primary_source_review_gate(project_id, model_id)
    columns = st.columns(3)
    columns[0].metric(
        "Propositions adjudicated",
        f"{gate['proposition_adjudicated']} / {gate['proposition_total']}",
    )
    columns[1].metric("Method sections", f"{gate['resolved_section_count']} / 4")
    columns[2].metric("Ready", "Yes" if gate["ready"] else "No")
    st.caption(f"Exact gate fingerprint: {gate['gate_fingerprint']}")
    if gate.get("completed_event") is not None:
        st.success(
            "The exact proposition and MethodModel review set is complete. Accepted "
            f"MethodModel: {model_id}"
        )
        return
    if gate["blocking_reasons"]:
        st.warning("Unresolved: " + ", ".join(map(str, gate["blocking_reasons"])))
    with st.form(f"primary-source-review-gate:{model_id}"):
        confirmed = st.checkbox(
            "I confirm that the displayed proposition set and all four complete MethodModel "
            "sections have been reviewed."
        )
        submitted = st.form_submit_button("Complete scientific review gate", type="primary")
    if submitted:
        _execute(
            lambda: client.complete_primary_source_review(
                project_id,
                {
                    "method_model_id": model_id,
                    "expected_gate_fingerprint": gate["gate_fingerprint"],
                    "confirmed": confirmed,
                },
                request_id=str(uuid4()),
            ),
            project_id=project_id,
            model_id=model_id,
            section="gate",
        )


def _render_post_mapping_coverage(
    client: ResearchFlowApiClient,
    *,
    project_id: str,
    model_id: str,
) -> None:
    st.subheader("Post-mapping MethodModel coverage")
    st.caption(
        "Reassessment uses the completed material mapping-review gate. Provider output cannot "
        "accept elements; any resulting section authority is derived from exact prior human review."
    )
    state_key = f"method-reassessment:{project_id}:{model_id}:result"
    with st.form(f"method-reassessment:{project_id}:{model_id}"):
        workflow_run_id = st.text_input("Workflow run UUID")
        mapping_gate_event_id = st.text_input("Completed mapping-review gate Event UUID")
        applicable = st.checkbox("Repository branch applicable", value=True)
        explicit_rerun = st.checkbox("Explicit provider rerun", value=False)
        submitted = st.form_submit_button("Reassess MethodModel")
    if submitted:
        try:
            response = client.reassess_method_model(
                project_id,
                {
                    "workflow_run_id": workflow_run_id,
                    "mapping_review_gate_event_id": mapping_gate_event_id,
                    "applicable": applicable,
                    "explicit_rerun": explicit_rerun,
                },
                request_id=str(uuid4()),
            )
        except ApiClientError as error:
            st.error(str(error))
        else:
            st.session_state[state_key] = response
            st.success("Post-mapping MethodModel reassessment recorded.")
    result_value = st.session_state.get(state_key)
    if isinstance(result_value, Mapping):
        display_result = cast(Mapping[str, Any], result_value)
        columns = st.columns(3)
        columns[0].metric("Outcome", _label(display_result["outcome"]))
        columns[1].metric("Changed elements", len(display_result["changed_element_ids"]))
        columns[2].metric("Carried section decisions", len(display_result["section_authority"]))
        st.caption(str(display_result["rationale"]))
        if display_result.get("authority_disclosure"):
            st.info(str(display_result["authority_disclosure"]))
        for section in display_result["section_authority"]:
            st.write(
                f"{_label(section['review_section'])}: prior human review "
                f"{section['source_section_review_id']} → "
                f"{section['resulting_section_review_id']}"
            )
            st.caption(str(section["rationale"]))


def render_method_review(
    client: ResearchFlowApiClient,
    *,
    project_id: str,
    chunk_locations: Mapping[str, Mapping[str, Any]],
) -> None:
    """Render server state without reproducing server review eligibility policy."""
    project_view = client.get_project(project_id)
    project = project_view["project"]
    question = project_view["primary_question"]
    st.caption(f"PROJECT / {str(project['id'])[:8]}")
    st.header(str(question["question_text"]))
    if question.get("scope_text"):
        st.write(str(question["scope_text"]))
    st.caption(
        f"Objective: {project['target_outcome']} · research/reproduction depth: "
        f"{project['reproduction_depth']}"
    )

    models = client.list_method_models(project_id, str(question["id"]))
    if not models:
        st.info("No candidate MethodModel is available for this Research Question.")
        return
    labels = {
        str(item["id"]): f"{item['name']} · v{item['version']} · {_label(item['review_status'])}"
        for item in models
    }
    model_id = st.selectbox(
        "MethodModel version",
        tuple(labels),
        format_func=labels.__getitem__,
        key=f"method-review:{project_id}:model",
    )
    detail = client.get_method_review(project_id, model_id)
    if len(detail["members"]) > 128:
        raise RuntimeError("Server returned a MethodModel above the approved aggregate bound.")
    st.subheader(str(detail["model"]["name"]))
    st.write(str(detail["model"]["objective"]))
    grouped = group_method_members(detail["members"])
    for section in METHOD_SECTION_ORDER:
        _render_section(
            client,
            project_id=project_id,
            detail=detail,
            section=section,
            members=grouped[section],
            chunk_locations=chunk_locations,
        )
    _render_aggregate(client, project_id=project_id, detail=detail)
    _render_post_mapping_coverage(
        client,
        project_id=project_id,
        model_id=model_id,
    )
    _render_combined_gate(client, project_id=project_id, model_id=model_id)
