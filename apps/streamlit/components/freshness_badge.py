"""Visual-only status labeling for immutable artifact versions."""

from collections.abc import Mapping
from typing import Any

import streamlit as st
from apps.streamlit.mental_model_view import status_label


def render_freshness_badge(model: Mapping[str, Any], *, is_current: bool) -> None:
    label = status_label(model, is_current=is_current)
    if label == "Current":
        st.success("Current structured version")
    elif str(model["status"]) == "stale":
        st.warning("Stale historical structure")
    else:
        st.info(f"{label} historical structure")
