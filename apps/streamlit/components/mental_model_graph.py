"""Supplementary static Mental Model relationship graph."""

from collections.abc import Mapping
from typing import Any

import streamlit as st
from apps.streamlit.mental_model_view import graph_rows, member_title


def _escape(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def mental_model_dot(detail: Mapping[str, Any]) -> str:
    model = detail["model"]
    question = detail["question"]
    method = detail["method_model"]
    labels = {
        str(question["id"]): f"Research Question\\n{question['question_text']}",
        str(method["id"]): f"MethodModel v{method['version']}\\n{method['name']}",
        str(model["id"]): f"Mental Model v{model['version']}\\n{model['title']}",
    }
    labels.update({str(item["member"]["id"]): member_title(item) for item in detail["members"]})
    lines = ["digraph mental_model {", 'rankdir="LR";', 'node [shape="box"];']
    for identifier, label in sorted(labels.items()):
        lines.append(f'"{identifier}" [label="{_escape(label)[:240]}"];')
    for source, target, label in graph_rows(detail):
        lines.append(f'"{source}" -> "{target}" [label="{_escape(label)}"];')
    lines.append("}")
    return "\n".join(lines)


def render_mental_model_graph(detail: Mapping[str, Any]) -> None:
    st.caption("Supplementary static relationship view. Layout is not canonical state.")
    st.graphviz_chart(mental_model_dot(detail), width="stretch")
