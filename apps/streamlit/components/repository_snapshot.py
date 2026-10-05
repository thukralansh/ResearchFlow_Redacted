"""Human-confirmed repository role and immutable commit selection UI."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast
from uuid import uuid4

import streamlit as st
from apps.streamlit.api_client import ResearchFlowApiClient
from apps.streamlit.components.repository_coverage import render_repository_coverage

RELATIONSHIP_TYPES = (
    "official_implementation",
    "author_affiliated_implementation",
    "official_supporting_code",
    "independent_reproduction",
    "community_implementation",
    "reference_framework",
    "partial_implementation",
    "related_method",
    "supporting_library",
    "unknown",
)
AUTHORITY_LEVELS = (
    "first_party",
    "author_affiliated",
    "independent",
    "contextual",
    "unknown",
)


@dataclass(frozen=True, slots=True)
class RepositoryDraftDefaults:
    repository_url: str
    requested_ref: str
    relationship_type: str
    authority_level: str
    rationale: str
    evidence_text: str
    confidence: float


def repository_widget_scope(
    *, project_id: str, source_id: object, paper_relationship_id: object
) -> str:
    """Bind repository draft widgets to one Project and immutable paper identity."""
    return f"repository-snapshot:{project_id}:{source_id}:{paper_relationship_id}"


def repository_draft_defaults(
    current: Mapping[str, Any] | None,
) -> RepositoryDraftDefaults:
    """Seed a draft from this Project's current relationship or neutral values."""
    if current is None:
        return RepositoryDraftDefaults(
            repository_url="",
            requested_ref="",
            relationship_type="unknown",
            authority_level="unknown",
            rationale="",
            evidence_text="",
            confidence=0.5,
        )
    repository = cast(Mapping[str, Any], current["repository"])
    snapshot = cast(Mapping[str, Any], current["snapshot"])
    relationship = cast(Mapping[str, Any], current["relationship"])
    return RepositoryDraftDefaults(
        repository_url=str(repository["normalized_url"]),
        requested_ref=str(snapshot["requested_ref"]),
        relationship_type=str(relationship["relationship_type"]),
        authority_level=str(relationship["authority_level"]),
        rationale=str(relationship["rationale"]),
        evidence_text=str(relationship.get("evidence_text") or ""),
        confidence=(
            float(relationship["confidence"]) if relationship.get("confidence") is not None else 0.5
        ),
    )


def render_repository_snapshot(
    client: ResearchFlowApiClient,
    *,
    project_id: str,
    primary_question: dict[str, object],
    source: dict[str, object],
    paper_relationship: dict[str, object],
) -> None:
    history = client.list_repository_history(project_id)
    current = next(
        (
            item
            for item in reversed(history)
            if item["relationship"]["review_status"] == "accepted"
            and item["relationship"]["lifecycle_status"] == "current"
            and str(item["relationship"]["source_id"]) == str(source["id"])
        ),
        None,
    )

    st.divider()
    st.subheader("Repository evidence source")
    st.caption(
        "A repository is implementation evidence. Its broad role and qualitative "
        "authority require human confirmation and never override paper evidence."
    )
    if current is not None:
        repository = current["repository"]
        snapshot = current["snapshot"]
        relationship = current["relationship"]
        st.markdown(f"**{repository['owner']}/{repository['name']}**")
        st.code(snapshot["commit_sha"])
        st.caption(
            f"{relationship['relationship_type']} · {relationship['authority_level']} · "
            f"requested ref `{snapshot['requested_ref']}`"
        )

    widget_scope = repository_widget_scope(
        project_id=project_id,
        source_id=source["id"],
        paper_relationship_id=paper_relationship["id"],
    )
    defaults = repository_draft_defaults(current)
    with st.form(f"{widget_scope}:form", clear_on_submit=False):
        repository_url = st.text_input(
            "Public GitHub repository URL",
            value=defaults.repository_url,
            max_chars=500,
            key=f"{widget_scope}:url",
        )
        requested_ref = st.text_input(
            "Git ref",
            value=defaults.requested_ref,
            max_chars=255,
            help="Use an immutable 40-character commit SHA for a pinned snapshot.",
            key=f"{widget_scope}:ref",
        )
        relationship_type = st.selectbox(
            "Relationship to the Primary Paper",
            RELATIONSHIP_TYPES,
            index=RELATIONSHIP_TYPES.index(defaults.relationship_type),
            key=f"{widget_scope}:relationship-type",
        )
        authority_level = st.selectbox(
            "Qualitative authority",
            AUTHORITY_LEVELS,
            index=AUTHORITY_LEVELS.index(defaults.authority_level),
            key=f"{widget_scope}:authority",
        )
        rationale = st.text_area(
            "Relationship rationale",
            value=defaults.rationale,
            max_chars=4_000,
            key=f"{widget_scope}:rationale",
        )
        evidence_text = st.text_area(
            "Authority evidence (optional)",
            value=defaults.evidence_text,
            max_chars=4_000,
            key=f"{widget_scope}:evidence",
        )
        confidence = st.slider(
            "Relationship confidence",
            0.0,
            1.0,
            defaults.confidence,
            0.05,
            key=f"{widget_scope}:confidence",
        )
        human_confirmed = st.checkbox(
            "I confirm this repository role and authority",
            help="Metadata or AI suggestions cannot accept this relationship.",
            key=f"{widget_scope}:confirmed",
        )
        replace_current = st.checkbox(
            "Replace the current repository snapshot and preserve history",
            disabled=current is None,
            key=f"{widget_scope}:replace",
        )
        submitted = st.form_submit_button("Resolve and attach snapshot", type="primary")

    if submitted:
        client.attach_repository(
            project_id,
            {
                "research_question_id": primary_question["id"],
                "primary_paper_relationship_id": paper_relationship["id"],
                "source_id": source["id"],
                "repository_url": repository_url,
                "requested_ref": requested_ref or None,
                "relationship_type": relationship_type,
                "authority_level": authority_level,
                "rationale": rationale,
                "evidence_text": evidence_text or None,
                "confidence": confidence,
                "human_confirmed": human_confirmed,
                "replace_current": replace_current,
            },
            idempotency_key=str(uuid4()),
        )
        st.success("Repository role confirmed and immutable snapshot stored.")
        st.rerun()

    if len(history) > 1:
        with st.expander(f"Repository relationship history ({len(history)})"):
            for item in history:
                st.caption(
                    f"v{item['relationship']['version']} · "
                    f"{item['repository']['owner']}/{item['repository']['name']} · "
                    f"{item['snapshot']['commit_sha']} · "
                    f"{item['relationship']['lifecycle_status']}"
                )

    if current is not None:
        render_repository_coverage(
            client,
            project_id=project_id,
            repository_view=current,
        )
