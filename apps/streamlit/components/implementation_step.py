"""Read-only implementation-step projection with typed human revision command."""

from typing import Any
from uuid import uuid4

import streamlit as st
from apps.streamlit.api_client import ResearchFlowApiClient


def render_implementation_step(
    client: ResearchFlowApiClient,
    *,
    project_id: str,
    step: dict[str, Any],
) -> None:
    title = f"{step['sequence_index']}. {step['title']}"
    with st.expander(title, expanded=True):
        st.caption(f"v{step['version']} · {step['lifecycle_status']} · {step['freshness_status']}")
        st.write(step["description"])
        if step["lifecycle_status"] == "current" and step["freshness_status"] == "current":
            with st.form(f"revise-step-{step['id']}"):
                revised_title = st.text_input("Revised title", value=step["title"])
                revised_description = st.text_area(
                    "Revised description", value=step["description"], max_chars=4_000
                )
                rationale = st.text_area("Revision rationale", max_chars=4_000)
                submitted = st.form_submit_button("Create immutable step revision")
            if submitted:
                client.revise_implementation_step(
                    project_id,
                    str(step["id"]),
                    {
                        "command_id": str(uuid4()),
                        "expected_version": step["version"],
                        "title": revised_title,
                        "description": revised_description,
                        "rationale": rationale,
                    },
                )
                st.rerun()
