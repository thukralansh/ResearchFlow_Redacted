"""Read-only immutable Blueprint history projection."""

from typing import Any

import streamlit as st


def changed_section_types(current: dict[str, Any], comparison: dict[str, Any]) -> tuple[str, ...]:
    """Return an exact rendering-only structural diff over persisted sections."""
    current_sections = {
        str(item["section_type"]): item["structured_content"] for item in current["sections"]
    }
    comparison_sections = {
        str(item["section_type"]): item["structured_content"] for item in comparison["sections"]
    }
    return tuple(
        sorted(
            section_type
            for section_type in current_sections.keys() | comparison_sections.keys()
            if current_sections.get(section_type) != comparison_sections.get(section_type)
        )
    )


def render_blueprint_history(history: list[dict[str, Any]], events: list[dict[str, Any]]) -> None:
    st.subheader("Version history")
    for item in history:
        blueprint = item["blueprint"]
        st.caption(
            f"v{blueprint['version']} · {blueprint['status']} · "
            f"eligibility {blueprint['eligibility_status']} · {blueprint['id']}"
        )
    if events:
        st.subheader("Review audit")
        for event in events:
            st.caption(f"{event['operation_type']} · {event['reason']}")
