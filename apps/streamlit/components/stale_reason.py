"""Read-only strongest-effective staleness explanation."""

from collections.abc import Mapping, Sequence
from typing import Any

import streamlit as st


def render_stale_reasons(freshness: Mapping[str, Any]) -> None:
    status = str(freshness["effective_status"])
    if status == "current":
        return
    if status == "invalid":
        st.error("Invalid: a confirmed integrity failure or unusable critical dependency exists.")
    elif status == "blocked":
        st.error("Blocked: an explicitly required prerequisite is missing.")
    else:
        st.warning("Stale: an exact accepted dependency changed.")
    values = freshness.get("invalidations", ())
    if isinstance(values, Sequence):
        with st.expander("Why this artifact is not current", expanded=True):
            for value in values:
                if isinstance(value, Mapping):
                    st.write(f"**{value['effect']}** · {value['reason']}")
                    st.caption(f"{value['rule_code']} · {value['cause_type']} {value['cause_id']}")
            st.caption("Direct persisted causes only; this is not a transitive impact summary.")
