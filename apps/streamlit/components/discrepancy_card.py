"""Read-only materiality and discrepancy inspection."""

from collections.abc import Mapping
from typing import Any

import streamlit as st


def render_discrepancy_card(discrepancy: Mapping[str, Any], *, key: str) -> None:
    materiality = discrepancy["materiality"]
    material_label = "Material" if materiality["material"] else "Non-material"
    with st.container(border=True):
        st.markdown(
            f"**{str(discrepancy['discrepancy_type']).replace('_', ' ').title()}** · "
            f"{material_label}"
        )
        st.write(str(discrepancy["explanation"]))
        st.caption(
            f"Materiality: {materiality['reason_code']} · {materiality['objective_consequence']}"
        )
        if discrepancy.get("bounded_limitation"):
            st.warning(str(discrepancy["bounded_limitation"]))
        with st.expander("Exact repository evidence", expanded=False):
            evidence = discrepancy.get("repository_evidence", [])
            if not evidence:
                st.caption("No component evidence was identified within the bounded inspection.")
            for index, item in enumerate(evidence):
                st.markdown(f"**{item['relation']} · component {item['repository_component_id']}**")
                st.code(str(item["excerpt"]))
                st.caption(f"{key}:{index}")
