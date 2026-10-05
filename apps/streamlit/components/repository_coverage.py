"""Truthful Repository Stage A coverage, exclusions, and structural evidence UI."""

from typing import Any, cast

import streamlit as st
from apps.streamlit.api_client import ResearchFlowApiClient
from apps.streamlit.components.repository_inspection import render_repository_inspection


def render_repository_coverage(
    client: ResearchFlowApiClient,
    *,
    project_id: str,
    repository_view: dict[str, object],
) -> None:
    relationship = cast(dict[str, Any], repository_view["relationship"])
    snapshot = cast(dict[str, Any], repository_view["snapshot"])
    history = client.list_repository_discoveries(project_id)
    latest = history[-1] if history else None

    st.divider()
    st.subheader("Structural repository coverage")
    st.caption(
        "Stage A uses paths, filenames, extensions, sizes, and repository metadata only. "
        "It does not use the research question, paper claims, MethodModel, or propositions."
    )
    if st.button("Run bounded Stage A discovery", type="primary"):
        latest = client.discover_repository(
            project_id,
            {
                "repository_snapshot_id": snapshot["id"],
                "paper_repository_relationship_id": relationship["id"],
            },
        )
        st.rerun()

    if latest is None:
        st.info("No structural discovery run exists for this Project.")
        render_repository_inspection(
            client,
            project_id=project_id,
            repository_view=repository_view,
        )
        return
    run = latest["run"]
    metrics = run["configured_limits"]
    columns = st.columns(4)
    columns[0].metric("Status", str(run["status"]))
    columns[1].metric("Coverage", str(run["coverage_status"]))
    columns[2].metric("Tree entries", int(metrics.get("tree_entry_count", 0)))
    columns[3].metric("Fetched files", int(metrics.get("selected_path_count", 0)))
    st.caption(
        f"{metrics.get('request_count', 0)} GitHub requests · "
        f"{metrics.get('response_bytes', 0)} response bytes · "
        f"tree cap {metrics.get('tree_entry_limit')} · "
        f"file cap {metrics.get('max_file_bytes')} bytes"
    )
    if run["status"] == "failed":
        st.error(f"{run['error_code']}: {run.get('error_detail') or 'Discovery failed.'}")
        render_repository_inspection(
            client,
            project_id=project_id,
            repository_view=repository_view,
        )
        return

    entries = latest["entries"]
    components = latest["components"]
    component_paths = {component["path"] for component in components}
    selected_rows = [
        {
            "path": entry["path"],
            "role": entry["coarse_role"],
            "language": entry["language"],
            "bytes": entry["size_bytes"],
        }
        for entry in entries
        if entry["path"] in component_paths
    ]
    excluded_rows = [
        {
            "path": entry["path"],
            "role": entry["coarse_role"],
            "reason": entry["excluded_reason"],
        }
        for entry in entries
        if entry["excluded_reason"] is not None
    ]
    st.markdown("**Fetched structural documents**")
    st.dataframe(selected_rows, width="stretch", hide_index=True)
    with st.expander(f"Excluded paths ({len(excluded_rows)})"):
        st.dataframe(excluded_rows, width="stretch", hide_index=True)
    if len(history) > 1:
        with st.expander(f"Discovery history ({len(history)})"):
            for item in history:
                prior = item["run"]
                st.caption(
                    f"{prior['discovery_version']} · {prior['status']} · "
                    f"{prior['coverage_status']} · {str(prior['id'])[:8]}"
                )
    render_repository_inspection(
        client,
        project_id=project_id,
        repository_view=repository_view,
    )
