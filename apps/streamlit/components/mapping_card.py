"""Side-by-side immutable mapping inspection and explicit lifecycle controls."""

from collections.abc import Callable, Mapping
from typing import Any
from uuid import uuid4

import streamlit as st


def render_mapping_card(
    mapping: Mapping[str, Any],
    *,
    key: str,
    mutate: Callable[[str, Mapping[str, object], str], None],
) -> None:
    anchor = mapping["anchor"]
    component = mapping.get("component")
    status = f"{mapping['review_status']}/{mapping['lifecycle_status']}"
    with st.container(border=True):
        st.markdown(
            f"**{str(mapping['mapping_type']).replace('_', ' ').title()}** · "
            f"v{mapping['version']} · `{status}`"
        )
        left, right = st.columns(2)
        with left:
            st.caption(f"Scientific anchor · {anchor['anchor_type']}")
            st.write(str(anchor["text"]))
            if anchor.get("source_location"):
                st.json(anchor["source_location"], expanded=False)
        with right:
            if component is None:
                scope = mapping.get("searched_scope") or {}
                st.caption("No corresponding component in the bounded inspection")
                st.write(str(scope.get("limitation", "Bounded inspection only.")))
                st.caption(
                    f"Coverage: {scope.get('coverage_status', 'unknown')} · "
                    f"{len(scope.get('inspected_paths', []))} inspected paths"
                )
            else:
                lines = (
                    f"lines {component['line_start']}-{component['line_end']}"
                    if component.get("line_start") is not None
                    else "bounded excerpt"
                )
                st.caption(f"Repository component · {component['path']} · {lines}")
                with st.expander("Exact inspected code evidence", expanded=False):
                    st.code(str(component["excerpt"]))
                    st.caption(f"SHA-256 {component['content_hash']}")
        st.write(str(mapping["explanation"]))
        rationale = st.text_area("Review rationale", key=f"{key}:rationale", max_chars=4_000)
        actions = _available_actions(
            str(mapping["review_status"]), str(mapping["lifecycle_status"])
        )
        columns = st.columns(max(1, len(actions)))
        for column, action in zip(columns, actions, strict=True):
            if column.button(action.replace("-", " ").title(), key=f"{key}:{action}"):
                if not rationale.strip():
                    st.error("A human review rationale is required.")
                    continue
                mutate(
                    action,
                    {
                        "command_id": str(uuid4()),
                        "expected_version": int(mapping["version"]),
                        "expected_review_status": str(mapping["review_status"]),
                        "expected_lifecycle_status": str(mapping["lifecycle_status"]),
                        "rationale": rationale.strip(),
                    },
                    f"mapping-review:{mapping['id']}:{action}:{uuid4()}",
                )


def _available_actions(review_status: str, lifecycle_status: str) -> tuple[str, ...]:
    if review_status == "candidate" and lifecycle_status == "current":
        return ("accept", "reject", "request-revision")
    if review_status == "accepted" and lifecycle_status == "current":
        return ("challenge",)
    if review_status == "accepted" and lifecycle_status == "challenged":
        return ("reaffirm",)
    return ()
