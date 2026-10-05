"""Read-only Blueprint section projection."""

from typing import Any

import streamlit as st


def render_blueprint_section(section: dict[str, Any]) -> None:
    """Render persisted structured content without deriving lifecycle semantics."""
    title = str(section["section_type"]).replace("_", " ").title()
    with st.expander(title, expanded=True):
        st.caption(
            f"Explicitness: {section['explicitness']} · "
            f"Review: {section['review_status']} · "
            f"Confidence: {section.get('confidence') or 'not supplied'}"
        )
        st.json(section["structured_content"], expanded=False)
        if section["section_type"] == "limitations_and_open_decisions":
            warnings = section["structured_content"].get("eligibility_warnings", [])
            for warning in warnings:
                if isinstance(warning, dict) and warning.get("statement"):
                    st.warning(str(warning["statement"]))
