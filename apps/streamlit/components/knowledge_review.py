"""Evidence-first proposition review cards backed only by  APIs."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any
from uuid import uuid4

import streamlit as st
from apps.streamlit.api_client import ApiClientError, ResearchFlowApiClient
from apps.streamlit.review_view import (
    ReviewFilters,
    filter_review_items,
    immutable_review_order,
    paginate_review_items,
    result_fingerprint,
    review_lineage_count,
    review_status_counts,
)

_OBJECT_TYPES = (
    "claim",
    "assumption",
    "knowledge_gap",
    "implementation_requirement",
    "ambiguity",
)
_REVIEW_STATUSES = ("candidate", "needs_revision", "accepted", "rejected")
_ORIGINS = ("source_extraction", "ai_inference", "repository_behavior", "human", "system")
_EPISTEMIC = ("explicit", "inferred", "speculative", "human_decided")


def _label(value: object) -> str:
    return str(value).replace("_", " ").title()


def _transition_actions(
    item: Mapping[str, Any], *, lineage: Sequence[Mapping[str, Any]] = ()
) -> tuple[str, ...]:
    state = (item["review_status"], item["lifecycle_status"])
    actions = {
        ("candidate", "current"): ("accept", "reject", "request-revision"),
        ("needs_revision", "current"): ("reject",),
        ("accepted", "current"): ("challenge",),
        ("accepted", "challenged"): ("reaffirm",),
    }.get(state, ())
    accepted_elsewhere = any(
        value["id"] != item["id"]
        and value["review_status"] == "accepted"
        and value["lifecycle_status"] in {"current", "challenged"}
        for value in lineage
    )
    return tuple(action for action in actions if not (action == "accept" and accepted_elsewhere))


def _base_command(item: Mapping[str, Any], rationale: str) -> dict[str, object]:
    return {
        "command_id": str(uuid4()),
        "expected_version": int(item["version"]),
        "expected_review_status": str(item["review_status"]),
        "expected_lifecycle_status": str(item["lifecycle_status"]),
        "rationale": rationale,
    }


def _execute_mutation(
    *,
    state_key: str,
    payload: dict[str, object],
    request_id: str,
    invoke: Callable[[Mapping[str, object], str], dict[str, Any]],
    success_message: str,
) -> None:
    try:
        invoke(payload, request_id)
    except ApiClientError as error:
        if error.retry_safe:
            st.session_state[state_key] = {
                "payload": payload,
                "request_id": request_id,
            }
            st.error(f"{error} The exact command is retained for a safe retry.")
        else:
            st.session_state.pop(state_key, None)
            st.error(str(error))
        return
    st.session_state.pop(state_key, None)
    st.success(success_message)
    st.rerun()


def _render_pending_retry(
    *,
    state_key: str,
    label: str,
    invoke: Callable[[Mapping[str, object], str], dict[str, Any]],
) -> bool:
    pending = st.session_state.get(state_key)
    if not isinstance(pending, dict):
        return False
    st.warning(
        "The previous outcome is unknown. Retry the exact retained command before continuing."
    )
    if st.button(f"Retry {label}", key=f"{state_key}:retry"):
        _execute_mutation(
            state_key=state_key,
            payload=dict(pending["payload"]),
            request_id=str(pending["request_id"]),
            invoke=invoke,
            success_message=f"{label} completed.",
        )
    return True


def _render_object_transition(
    client: ResearchFlowApiClient,
    *,
    project_id: str,
    item: Mapping[str, Any],
    lineage: Sequence[Mapping[str, Any]],
) -> None:
    actions = _transition_actions(item, lineage=lineage)
    if not actions:
        return
    object_id = str(item["id"])
    state_key = f"knowledge-review:{object_id}:transition"

    def invoke(payload: Mapping[str, object], request_id: str) -> dict[str, Any]:
        return client.review_knowledge(
            project_id,
            object_id,
            str(payload["action"]),
            {key: value for key, value in payload.items() if key != "action"},
            request_id=request_id,
        )

    if _render_pending_retry(state_key=state_key, label="review command", invoke=invoke):
        return
    with st.form(f"{state_key}:form"):
        action = st.selectbox("Review action", actions, format_func=_label)
        rationale = st.text_area("Decision rationale", max_chars=4_000)
        submitted = st.form_submit_button("Apply review decision", type="primary")
    if submitted:
        payload = {**_base_command(item, rationale), "action": action}
        request_id = str(uuid4())
        _execute_mutation(
            state_key=state_key,
            payload=payload,
            request_id=request_id,
            invoke=invoke,
            success_message=f"{_label(action)} recorded.",
        )


def _render_evidence(
    client: ResearchFlowApiClient,
    *,
    project_id: str,
    item: Mapping[str, Any],
    evidence: Sequence[Mapping[str, Any]],
    chunk_locations: Mapping[str, Mapping[str, Any]],
) -> None:
    st.markdown("**Evidence**")
    if not evidence:
        st.warning("No EvidenceLink is available for this candidate.")
        return
    for link in evidence:
        link_id = str(link["id"])
        location = chunk_locations.get(str(link["source_chunk_id"]))
        repository_component_id = link.get("repository_component_id")
        if repository_component_id is not None:
            location_text = f"RepositoryComponent {repository_component_id}"
        elif location is not None:
            section = location.get("section") or "Unsectioned"
            subsection = location.get("subsection")
            location_text = f"Page {location['page_number']} · {section}"
            if subsection:
                location_text += f" · {subsection}"
        else:
            location_text = "Exact source location unavailable"
        with st.container(border=True):
            st.caption(
                f"{_label(link['evidence_relation'])} · {location_text} · "
                f"{_label(link['review_status'])}/{_label(link['lifecycle_status'])}"
            )
            st.write(link["evidence_excerpt"])
            if location is None and repository_component_id is None:
                st.warning("The cited SourceChunk could not be resolved in current source history.")
            actions = _transition_actions(link)
            if not actions:
                continue
            state_key = f"knowledge-review:{item['id']}:evidence:{link_id}"

            def invoke(
                payload: Mapping[str, object], request_id: str, *, evidence_id: str = link_id
            ) -> dict[str, Any]:
                return client.review_evidence(
                    project_id,
                    str(item["id"]),
                    evidence_id,
                    str(payload["action"]),
                    {key: value for key, value in payload.items() if key != "action"},
                    request_id=request_id,
                )

            if _render_pending_retry(state_key=state_key, label="evidence review", invoke=invoke):
                continue
            with st.form(f"{state_key}:form"):
                action = st.selectbox(
                    "Evidence action", actions, format_func=_label, key=f"{state_key}:action"
                )
                rationale = st.text_area(
                    "Evidence rationale", max_chars=4_000, key=f"{state_key}:rationale"
                )
                submitted = st.form_submit_button("Review evidence")
            if submitted:
                payload = {**_base_command(link, rationale), "action": action}
                _execute_mutation(
                    state_key=state_key,
                    payload=payload,
                    request_id=str(uuid4()),
                    invoke=invoke,
                    success_message="Evidence review recorded and grounding refreshed.",
                )


def _select_value(label: str, options: Sequence[str], current: object, *, key: str) -> str:
    current_value = str(current)
    index = options.index(current_value) if current_value in options else 0
    return str(st.selectbox(label, options, index=index, format_func=_label, key=key))


def _subtype_editor(
    object_type: str,
    payload: Mapping[str, Any],
    interpretations: Sequence[Mapping[str, Any]],
    key: str,
) -> dict[str, object]:
    if object_type == "claim":
        return {
            "claim_type": _select_value(
                "Claim type",
                (
                    "scientific",
                    "mathematical",
                    "methodological",
                    "empirical",
                    "implementation",
                    "limitation",
                ),
                payload["claim_type"],
                key=f"{key}:claim-type",
            ),
            "verification_status": _select_value(
                "Verification status",
                ("unverified", "verified", "contradicted", "inconclusive"),
                payload["verification_status"],
                key=f"{key}:verification",
            ),
        }
    if object_type == "assumption":
        return {
            "assumption_type": _select_value(
                "Assumption type",
                (
                    "theoretical",
                    "mathematical",
                    "computational",
                    "data",
                    "experimental",
                    "implementation",
                ),
                payload["assumption_type"],
                key=f"{key}:assumption-type",
            ),
            "risk_level": _select_value(
                "Risk",
                ("low", "medium", "high", "critical"),
                payload["risk_level"],
                key=f"{key}:risk",
            ),
            "validation_status": _select_value(
                "Validation",
                ("unvalidated", "validated", "invalidated", "inconclusive"),
                payload["validation_status"],
                key=f"{key}:validation",
            ),
        }
    if object_type == "knowledge_gap":
        return {
            "gap_type": _select_value(
                "Gap type",
                (
                    "conceptual",
                    "mathematical",
                    "computational",
                    "evidentiary",
                    "literature",
                    "data",
                    "experimental",
                    "repository",
                ),
                payload["gap_type"],
                key=f"{key}:gap-type",
            ),
            "priority": _select_value(
                "Priority",
                ("low", "medium", "high", "critical"),
                payload["priority"],
                key=f"{key}:priority",
            ),
            "resolution_notes": st.text_area(
                "Resolution notes",
                value=str(payload.get("resolution_notes") or ""),
                max_chars=4_000,
                key=f"{key}:resolution-notes",
            )
            or None,
        }
    if object_type == "implementation_requirement":
        return {
            "category": _select_value(
                "Category",
                (
                    "mathematical",
                    "data",
                    "preprocessing",
                    "algorithm",
                    "architecture",
                    "training",
                    "evaluation",
                    "environment",
                    "compute",
                    "reproducibility",
                ),
                payload["category"],
                key=f"{key}:category",
            ),
            "explicitness": _select_value(
                "Explicitness",
                (
                    "explicit",
                    "inferred_from_paper",
                    "inferred_from_repository",
                    "inferred_from_convention",
                    "human_decided",
                    "speculative",
                ),
                payload["explicitness"],
                key=f"{key}:explicitness",
            ),
            "priority": _select_value(
                "Priority",
                ("low", "medium", "high", "critical"),
                payload["priority"],
                key=f"{key}:priority",
            ),
            "satisfaction_status": _select_value(
                "Satisfaction",
                ("unsatisfied", "partially_satisfied", "satisfied", "not_applicable"),
                payload["satisfaction_status"],
                key=f"{key}:satisfaction",
            ),
        }
    edited_interpretations: list[dict[str, object]] = []
    for index, interpretation in enumerate(interpretations):
        st.caption(f"Interpretation {index + 1}")
        edited_interpretations.append(
            {
                "title": st.text_input(
                    "Title",
                    value=str(interpretation["title"]),
                    max_chars=500,
                    key=f"{key}:i:{index}:title",
                ),
                "description": st.text_area(
                    "Description",
                    value=str(interpretation["description"]),
                    max_chars=4_000,
                    key=f"{key}:i:{index}:description",
                ),
                "implementation_consequences": st.text_area(
                    "Implementation consequences",
                    value=str(interpretation["implementation_consequences"]),
                    max_chars=4_000,
                    key=f"{key}:i:{index}:consequences",
                ),
                "rank": int(interpretation["rank"]),
            }
        )
    return {
        "ambiguity_type": _select_value(
            "Ambiguity type",
            (
                "missing_detail",
                "conflicting_sources",
                "undefined_terminology",
                "paper_code_discrepancy",
                "mathematical",
                "algorithmic",
                "data",
                "framework",
                "evaluation",
                "environment",
            ),
            payload["ambiguity_type"],
            key=f"{key}:ambiguity-type",
        ),
        "impact_level": _select_value(
            "Impact",
            ("low", "medium", "high", "critical"),
            payload["impact_level"],
            key=f"{key}:impact",
        ),
        "resolution_status": _select_value(
            "Resolution",
            ("unresolved", "judgment_requested", "resolved"),
            payload["resolution_status"],
            key=f"{key}:resolution",
        ),
        "interpretations": edited_interpretations,
    }


def _render_semantic_revision(
    client: ResearchFlowApiClient,
    *,
    project_id: str,
    detail: Mapping[str, Any],
) -> None:
    item = detail["object"]
    if item["lifecycle_status"] == "superseded" or item["review_status"] == "rejected":
        return
    object_id = str(item["id"])
    state_key = f"knowledge-review:{object_id}:semantic"

    def invoke(payload: Mapping[str, object], request_id: str) -> dict[str, Any]:
        return client.create_semantic_revision(
            project_id, object_id, payload, request_id=request_id
        )

    with st.expander("Create semantic revision"):
        if _render_pending_retry(state_key=state_key, label="semantic revision", invoke=invoke):
            return
        with st.form(f"{state_key}:form"):
            statement = st.text_area(
                "Canonical statement", value=str(item["statement"]), max_chars=2_000
            )
            origin = _select_value("Origin", _ORIGINS, item["origin"], key=f"{state_key}:origin")
            epistemic = _select_value(
                "Epistemic status",
                _EPISTEMIC,
                item["epistemic_status"],
                key=f"{state_key}:epistemic",
            )
            confidence_unknown = st.checkbox(
                "Confidence not supplied",
                value=item.get("confidence") is None,
                key=f"{state_key}:confidence-unknown",
            )
            confidence = st.number_input(
                "Model confidence (metadata, not truth probability)",
                min_value=0.0,
                max_value=1.0,
                value=float(item.get("confidence") or 0.0),
                step=0.01,
                disabled=confidence_unknown,
            )
            subtype = _subtype_editor(
                str(item["object_type"]),
                detail["subtype_payload"],
                detail["ambiguity_interpretations"],
                state_key,
            )
            eligible_evidence = tuple(
                link
                for link in detail["evidence"]
                if link["lifecycle_status"] == "current"
                and link["review_status"] in {"candidate", "needs_revision", "accepted"}
            )
            reproposed_evidence_link_ids = tuple(
                str(link["id"])
                for link in eligible_evidence
                if st.checkbox(
                    "Re-propose exact evidence "
                    f"{str(link['id'])[:8]} ({_label(link['review_status'])})",
                    value=False,
                    key=f"{state_key}:evidence:{link['id']}",
                    help=(
                        "Creates a new candidate EvidenceLink for the revision with the exact "
                        "same immutable anchor. Prior review status is never inherited."
                    ),
                )
            )
            rationale = st.text_area("Revision rationale", max_chars=4_000)
            submitted = st.form_submit_button("Create candidate revision")
        if submitted:
            payload = {
                **_base_command(item, rationale),
                "object_type": item["object_type"],
                "statement": statement,
                "origin": origin,
                "epistemic_status": epistemic,
                "confidence": None if confidence_unknown else confidence,
                "subtype": subtype,
                "reproposed_evidence_link_ids": list(reproposed_evidence_link_ids),
            }
            _execute_mutation(
                state_key=state_key,
                payload=payload,
                request_id=str(uuid4()),
                invoke=invoke,
                success_message="A new immutable candidate version was created.",
            )


def _render_presentation_revision(
    client: ResearchFlowApiClient,
    *,
    project_id: str,
    detail: Mapping[str, Any],
) -> None:
    item = detail["object"]
    if item["lifecycle_status"] == "superseded":
        return
    object_id = str(item["id"])
    state_key = f"knowledge-review:{object_id}:presentation"

    def invoke(payload: Mapping[str, object], request_id: str) -> dict[str, Any]:
        return client.create_presentation_revision(
            project_id, object_id, payload, request_id=request_id
        )

    current = detail["presentations"][-1] if detail["presentations"] else None
    with st.expander("Edit presentation only"):
        st.caption("This changes display text, never the canonical scientific statement.")
        if _render_pending_retry(state_key=state_key, label="presentation revision", invoke=invoke):
            return
        with st.form(f"{state_key}:form"):
            display_text = st.text_area(
                "Display text",
                value=str(current["display_text"] if current else item["statement"]),
                max_chars=4_000,
            )
            display_label = st.text_input(
                "Display label",
                value=str((current or {}).get("display_label") or ""),
                max_chars=500,
            )
            rationale = st.text_area("Presentation rationale", max_chars=4_000)
            submitted = st.form_submit_button("Save presentation revision")
        if submitted:
            payload = {
                **_base_command(item, rationale),
                "display_text": display_text,
                "display_label": display_label or None,
            }
            _execute_mutation(
                state_key=state_key,
                payload=payload,
                request_id=str(uuid4()),
                invoke=invoke,
                success_message="Presentation revision saved without changing canonical meaning.",
            )


def _render_accept_revision(
    client: ResearchFlowApiClient,
    *,
    project_id: str,
    detail: Mapping[str, Any],
) -> None:
    item = detail["object"]
    if item["review_status"] != "candidate" or not item.get("derived_from_id"):
        return
    lineage_by_id = {str(value["id"]): value for value in detail["lineage"]}
    predecessor = None
    cursor = item
    visited: set[str] = set()
    while cursor.get("derived_from_id") is not None:
        parent_id = str(cursor["derived_from_id"])
        if parent_id in visited:
            break
        visited.add(parent_id)
        parent = lineage_by_id.get(parent_id)
        if parent is None:
            break
        if parent["review_status"] == "accepted" and parent["lifecycle_status"] in {
            "current",
            "challenged",
        }:
            predecessor = parent
            break
        cursor = parent
    if predecessor is None:
        return
    state_key = f"knowledge-review:{item['id']}:accept-revision"

    def invoke(payload: Mapping[str, object], request_id: str) -> dict[str, Any]:
        return client.accept_knowledge_revision(
            project_id, str(predecessor["id"]), payload, request_id=request_id
        )

    if _render_pending_retry(state_key=state_key, label="revision acceptance", invoke=invoke):
        return
    with st.form(f"{state_key}:form"):
        st.caption(
            f"Accept version {item['version']} and supersede version {predecessor['version']}."
        )
        rationale = st.text_area("Revision acceptance rationale", max_chars=4_000)
        submitted = st.form_submit_button("Accept this revision", type="primary")
    if submitted:
        payload = {
            "command_id": str(uuid4()),
            "replacement_id": item["id"],
            "expected_predecessor_version": predecessor["version"],
            "expected_predecessor_lifecycle_status": predecessor["lifecycle_status"],
            "expected_replacement_version": item["version"],
            "rationale": rationale,
        }
        _execute_mutation(
            state_key=state_key,
            payload=payload,
            request_id=str(uuid4()),
            invoke=invoke,
            success_message="Revision accepted and predecessor superseded atomically.",
        )


def _render_history(detail: Mapping[str, Any]) -> None:
    with st.expander("Version and audit history"):
        st.markdown("**Semantic lineage**")
        for version in detail["lineage"]:
            st.caption(
                f"v{version['version']} · {_label(version['review_status'])}/"
                f"{_label(version['lifecycle_status'])} · {str(version['id'])[:8]}"
            )
        st.markdown("**Human decisions**")
        if not detail["history"]:
            st.caption("No review decisions recorded yet.")
        for event in detail["history"]:
            st.caption(f"{event['operation_type']} · {str(event['id'])[:8]}")
            if event.get("reason"):
                st.write(event["reason"])


def _render_card(
    client: ResearchFlowApiClient,
    *,
    project_id: str,
    item: Mapping[str, Any],
    chunk_locations: Mapping[str, Mapping[str, Any]],
) -> None:
    title = (
        f"{_label(item['object_type'])} · v{item['version']} · "
        f"{_label(item['review_status'])}/{_label(item['lifecycle_status'])}"
    )
    with st.expander(title):
        st.write(item["statement"])
        badges = st.columns(4)
        badges[0].metric("Epistemic", _label(item["epistemic_status"]))
        badges[1].metric("Grounding", _label(item["grounding_status"]))
        badges[2].metric("Origin", _label(item["origin"]))
        badges[3].metric(
            "Model confidence",
            "Not supplied" if item.get("confidence") is None else item["confidence"],
        )
        detail = client.get_knowledge(project_id, str(item["id"]))
        _render_evidence(
            client,
            project_id=project_id,
            item=item,
            evidence=detail["evidence"],
            chunk_locations=chunk_locations,
        )
        _render_history(detail)
        st.caption(f"Lineage {str(item['lineage_id'])[:8]} · semantic version {item['version']}")
        _render_object_transition(
            client,
            project_id=project_id,
            item=item,
            lineage=detail["lineage"],
        )
        _render_accept_revision(client, project_id=project_id, detail=detail)
        _render_semantic_revision(client, project_id=project_id, detail=detail)
        _render_presentation_revision(client, project_id=project_id, detail=detail)


def _render_accepted_preview(client: ResearchFlowApiClient, *, project_id: str) -> None:
    accepted = sorted(
        client.list_accepted_current_knowledge(project_id), key=immutable_review_order
    )
    st.caption(
        "Server-authoritative accepted/current synthesis view. Challenged, rejected, "
        "superseded, needs-revision, and candidate objects are excluded by the backend."
    )
    st.metric("Accepted/current objects", len(accepted))
    if not accepted:
        st.info("No proposition is currently eligible for accepted-only synthesis.")
    for item in accepted:
        st.markdown(f"- **{_label(item['object_type'])}:** {item['statement']}")


def render_knowledge_review(
    client: ResearchFlowApiClient,
    *,
    project_id: str,
    chunk_locations: Mapping[str, Mapping[str, Any]],
) -> None:
    """Render the complete  review workspace without computing a gate."""
    review_tab, accepted_tab = st.tabs(("Review candidates", "Accepted-only preview"))
    with accepted_tab:
        _render_accepted_preview(client, project_id=project_id)
    with review_tab:
        items = client.list_knowledge(project_id)
        counts = review_status_counts(items)
        metrics = st.columns(5)
        metrics[0].metric("Semantic lineages", review_lineage_count(items))
        for column, status in zip(metrics[1:], _REVIEW_STATUSES, strict=True):
            column.metric(f"{_label(status)} versions", counts[status])
        st.caption(
            "Status counts describe immutable semantic versions, not distinct lineages. "
            "They are not review-gate authority."
        )

        filter_columns = st.columns(3)
        object_types = filter_columns[0].multiselect(
            "Knowledge type", _OBJECT_TYPES, format_func=_label
        )
        statuses = filter_columns[1].multiselect(
            "Review status", _REVIEW_STATUSES, format_func=_label
        )
        generation_ids = sorted(
            {
                str(item["generation_record_id"])
                for item in items
                if item.get("generation_record_id")
            }
        )
        generations = filter_columns[2].multiselect(
            "Extraction generation",
            generation_ids,
            format_func=lambda value: value[:8],
        )
        search = st.text_input("Search statements", max_chars=500)
        filters = ReviewFilters(
            object_types=frozenset(object_types),
            review_statuses=frozenset(statuses),
            generation_record_ids=frozenset(generations),
            search_text=search,
        )
        filtered = filter_review_items(items, filters)
        fingerprint = result_fingerprint(filtered, filters)
        page_state_key = f"knowledge-review:{project_id}:page"
        fingerprint_state_key = f"knowledge-review:{project_id}:fingerprint"
        prior_fingerprint = st.session_state.get(fingerprint_state_key)
        if prior_fingerprint != fingerprint:
            st.session_state[page_state_key] = 1
            st.session_state[fingerprint_state_key] = fingerprint
        requested_page = int(st.session_state.get(page_state_key, 1))
        page = paginate_review_items(
            filtered, requested_page=requested_page, fingerprint=fingerprint
        )
        st.session_state[page_state_key] = page.page_number
        st.caption(
            f"Showing {len(page.items)} of {page.total_items} filtered results · "
            f"page {page.page_number} of {page.page_count} · 32 cards maximum per page. "
            "Every filtered result remains available across these display-only pages."
        )
        if page.page_count > 1:
            selected_page = st.number_input(
                "Review page",
                min_value=1,
                max_value=page.page_count,
                value=page.page_number,
                step=1,
            )
            if int(selected_page) != page.page_number:
                st.session_state[page_state_key] = int(selected_page)
                st.rerun()
        if not page.items:
            st.info("No candidates match the current filters.")
        for item in page.items:
            try:
                _render_card(
                    client,
                    project_id=project_id,
                    item=item,
                    chunk_locations=chunk_locations,
                )
            except ApiClientError as error:
                st.error(f"Could not load candidate {str(item['id'])[:8]}: {error}")


def chunk_location_map(
    client: ResearchFlowApiClient, *, project_id: str
) -> dict[str, Mapping[str, Any]]:
    """Resolve exact page/section metadata through existing source APIs."""
    locations: dict[str, Mapping[str, Any]] = {}
    for source in client.list_sources(project_id):
        for processing in client.list_source_processing_runs(
            project_id, str(source["source"]["id"])
        ):
            for chunk in processing["chunks"]:
                locations[str(chunk["id"])] = chunk
    return locations
