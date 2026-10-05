"""Read-only renderer for one server-owned Blueprint eligibility finding."""

from typing import Any

import streamlit as st


def render_eligibility_item(item: dict[str, Any]) -> None:
    criticality = str(item["criticality"]).replace("_", " ").title()
    outcome = str(item["outcome"]).replace("_", " ").title()
    st.markdown(f"**{criticality} · {outcome}** — `{item['reason_code']}`")
    st.write(item["explanation"])
    target = item.get("target_id")
    if target:
        st.caption(f"{item['expected_dependency_type']}: {target}")
