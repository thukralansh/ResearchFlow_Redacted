"""Exact SourceChunk provenance drawer for Mental Model inspection."""

from collections.abc import Mapping, Sequence
from typing import Any

import streamlit as st


def render_provenance_drawer(evidence: Sequence[Mapping[str, Any]], *, key: str) -> None:
    with st.expander(f"Evidence provenance ({len(evidence)})", expanded=False):
        if not evidence:
            st.caption("No direct EvidenceLink is attached to this member.")
            return
        for index, item in enumerate(evidence):
            chunk = item.get("source_chunk")
            component = item.get("repository_component")
            if component is not None:
                lines = (
                    f"lines {component['line_start']}-{component['line_end']}"
                    if component.get("line_start") is not None
                    else "bounded component excerpt"
                )
                st.markdown(
                    f"**{str(item['relation']).replace('_', ' ').title()} · "
                    f"{component['path']} · {lines}**"
                )
                st.caption(
                    f"Evidence {str(item['id'])[:12]}… · "
                    f"{item['review_status']}/{item['lifecycle_status']} · repository component"
                )
                st.write(str(item["excerpt"]))
                with st.expander("Exact inspected repository excerpt", expanded=False):
                    st.code(str(component["content_excerpt"]))
                    st.caption(f"SHA-256 {component['content_hash']} · {key}:{index}")
                continue
            if chunk is None:
                st.error("Evidence provenance is unavailable.")
                continue
            section = chunk.get("subsection") or chunk.get("section") or "Unsectioned"
            st.markdown(
                f"**{str(item['relation']).replace('_', ' ').title()} · "
                f"page {chunk['page_number']} · {section}**"
            )
            st.caption(
                f"Evidence {str(item['id'])[:12]}… · "
                f"{item['review_status']}/{item['lifecycle_status']} · "
                f"chunk {chunk['chunk_index']} · offsets "
                f"[{chunk['start_offset']}, {chunk['end_offset']})"
            )
            st.write(str(item["excerpt"]))
            with st.expander("Exact normalized SourceChunk", expanded=False):
                st.text(str(chunk["chunk_text"]))
                st.caption(f"SHA-256 {chunk['content_hash']} · {key}:{index}")
