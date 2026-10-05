"""Read-only VerificationCheck projection with explicit human result command."""

from typing import Any
from uuid import uuid4

import streamlit as st
from apps.streamlit.api_client import ResearchFlowApiClient


def render_verification_check(
    client: ResearchFlowApiClient,
    *,
    project_id: str,
    check: dict[str, Any],
) -> None:
    st.markdown(f"**Check {check['sequence_index']} · {check['test_type']}**")
    st.write(check["condition"])
    st.caption(f"Expected: {check['expected_result']}")
    st.caption(f"Failure means: {check['failure_meaning']}")
    st.caption("Likely causes: " + "; ".join(check["likely_causes"]))
    st.caption(
        f"Lifecycle: {check['lifecycle_status']} · freshness: {check['freshness_status']} · "
        f"result: {check.get('result_status') or 'not recorded'}"
    )
    if check.get("actual_result"):
        st.write(f"Recorded result: {check['actual_result']}")
    if check["lifecycle_status"] == "current" and check["freshness_status"] == "current":
        with st.form(f"record-check-{check['id']}"):
            status = st.selectbox(
                "Result status",
                ["passed", "failed", "inconclusive"],
                key=f"status-{check['id']}",
            )
            actual = st.text_area("Actual result", max_chars=4_000)
            rationale = st.text_area("Result rationale", max_chars=4_000)
            submitted = st.form_submit_button("Record result")
        if submitted:
            client.record_verification_result(
                project_id,
                str(check["id"]),
                {
                    "command_id": str(uuid4()),
                    "expected_result_status": check.get("result_status"),
                    "result_status": status,
                    "actual_result": actual,
                    "rationale": rationale,
                },
            )
            st.rerun()
