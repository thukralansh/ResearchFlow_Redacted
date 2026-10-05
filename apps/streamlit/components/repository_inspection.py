"""Truthful applicable/not-applicable Repository Stage B inspection surface."""

from typing import Any, cast

import streamlit as st
from apps.streamlit.api_client import ResearchFlowApiClient


def render_repository_inspection(
    client: ResearchFlowApiClient,
    *,
    project_id: str,
    repository_view: dict[str, object] | None,
) -> None:
    st.divider()
    st.subheader("Knowledge-guided repository inspection")
    st.caption(
        "Stage B ranks immutable Stage A inventory from the current Research Question, "
        "accepted project knowledge, accepted MethodModel, initial Mental Model, and "
        "confirmed repository role. Scores prioritize inspection only; they do not "
        "establish truth, mapping validity, or canonical knowledge."
    )
    history = client.list_repository_inspections(project_id)
    rationale = st.text_area(
        "Repository-branch applicability rationale",
        key=f"stage-b-rationale:{project_id}",
        max_chars=4_000,
    )
    if repository_view is None:
        if st.button("Record repository branch as not applicable", type="primary"):
            client.inspect_repository(
                project_id,
                {
                    "applicable": False,
                    "applicability_rationale": rationale,
                },
            )
            st.success("Repository evidence branch recorded as not applicable.")
        st.info("No repository state is fabricated for a not-applicable branch.")
        return

    relationship = cast(dict[str, Any], repository_view["relationship"])
    snapshot = cast(dict[str, Any], repository_view["snapshot"])
    discoveries = client.list_repository_discoveries(project_id)
    discovery = next(
        (item for item in reversed(discoveries) if item["run"]["status"] == "succeeded"),
        None,
    )
    columns = st.columns(2)
    if columns[0].button("Run bounded Stage B inspection", type="primary"):
        if discovery is None:
            st.error("A successful current Stage A discovery is required first.")
        else:
            client.inspect_repository(
                project_id,
                {
                    "applicable": True,
                    "applicability_rationale": rationale,
                    "repository_snapshot_id": snapshot["id"],
                    "paper_repository_relationship_id": relationship["id"],
                    "discovery_run_id": discovery["run"]["id"],
                },
            )
            st.rerun()
    if columns[1].button("Record branch as not applicable"):
        client.inspect_repository(
            project_id,
            {
                "applicable": False,
                "applicability_rationale": rationale,
            },
        )
        st.success(
            "Repository branch recorded as not applicable for the current objective; "
            "existing repository evidence remains preserved."
        )

    if not history:
        st.info("No Stage B inspection exists for this Project.")
        return
    latest = history[-1]
    run = latest["run"]
    metrics = run["configured_limits"]
    summary = st.columns(4)
    summary[0].metric("Status", run["status"])
    summary[1].metric("Coverage", run["coverage_status"])
    summary[2].metric("Ranked candidates", metrics.get("candidate_count", 0))
    summary[3].metric("Inspected files", metrics.get("inspected_path_count", 0))
    st.caption(
        f"{metrics.get('content_bytes', 0)} / {metrics.get('aggregate_bytes', 0)} bytes · "
        f"ranking {run['ranking_version']} · analysis {run['analysis_version']} · "
        f"guidance {run['guidance_fingerprint'][:12]}"
    )
    ranking = latest["ranking"]
    st.markdown("**Ranking and selection diagnostics**")
    st.dataframe(
        [
            {
                "path": item["path"],
                "score": item["score"],
                "selected": item["selected"],
                "reason": item["selection_reason"],
                "matched terms": ", ".join(item["matched_terms"]),
                "matched roles": ", ".join(item["matched_roles"]),
            }
            for item in ranking
        ],
        width="stretch",
        hide_index=True,
    )
    with st.expander(f"Exact repository components ({len(latest['components'])})"):
        st.dataframe(
            [
                {
                    "path": item["path"],
                    "symbol": item["symbol"],
                    "type": item["component_type"],
                    "lines": f"{item['line_start']}-{item['line_end']}",
                    "score": item["relevance_score"],
                    "hash": item["content_hash"],
                }
                for item in latest["components"]
            ],
            width="stretch",
            hide_index=True,
        )
