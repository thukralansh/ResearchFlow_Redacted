"""Combined mapping, derived consequence, and material review-gate workspace."""

import os
import sys
from collections.abc import Mapping
from pathlib import Path
from uuid import UUID, uuid4

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from apps.streamlit.api_client import ApiClientError, ResearchFlowApiClient  # noqa: E402
from apps.streamlit.components.discrepancy_card import render_discrepancy_card  # noqa: E402
from apps.streamlit.components.mapping_card import render_mapping_card  # noqa: E402

API_URL = os.getenv("RESEARCHFLOW_API_URL", "http://127.0.0.1:8000")

st.set_page_config(page_title="ResearchFlow · Mapping review", page_icon="RF", layout="wide")
st.title("Repository mapping and consequence review")
st.caption(
    "Inspect exact scientific/code anchors, review mapping-derived candidates through their "
    "normal lifecycle surfaces, and complete the gate only after material state is adjudicated."
)


def _valid_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


project_id = st.session_state.get("selected_project_id")
if not isinstance(project_id, str):
    st.info("Open or create a project from the ResearchFlow home page first.")
else:
    active_project_id = project_id
    client = ResearchFlowApiClient(API_URL)
    try:
        mappings = client.list_mappings(active_project_id)
        st.subheader(f"Paper-to-code mappings ({len(mappings)})")

        for mapping in mappings:
            current_mapping_id = str(mapping["id"])

            def mutate(
                action: str,
                payload: Mapping[str, object],
                request_id: str,
                *,
                mapping_id: str = current_mapping_id,
            ) -> None:
                client.review_mapping(
                    active_project_id, mapping_id, action, payload, request_id=request_id
                )
                st.rerun()

            render_mapping_card(mapping, key=f"mapping:{current_mapping_id}", mutate=mutate)

        st.divider()
        st.subheader("Derive mapping consequences")
        inspections = client.list_repository_inspections(active_project_id)
        successful = [item for item in inspections if item.get("status") == "succeeded"]
        with st.form("mapping-derivation"):
            workflow_id = st.text_input("Workflow run UUID")
            applicable = st.checkbox("Repository branch is applicable", value=True)
            inspection_id = st.selectbox(
                "Successful inspection run",
                options=[str(item["id"]) for item in successful],
                disabled=not applicable,
            )
            applicability_rationale = st.text_area("Applicability rationale", max_chars=4_000)
            explicit_rerun = st.checkbox("Explicit rerun", value=False)
            submitted = st.form_submit_button("Derive candidate consequences")
        if submitted:
            if not _valid_uuid(workflow_id) or not applicability_rationale.strip():
                st.error("A valid workflow UUID and rationale are required.")
            elif applicable and not inspection_id:
                st.error("An exact successful inspection run is required.")
            else:
                result = client.derive_mapping_knowledge(
                    active_project_id,
                    {
                        "workflow_run_id": workflow_id,
                        "repository_inspection_run_id": inspection_id if applicable else None,
                        "applicable": applicable,
                        "applicability_rationale": applicability_rationale.strip(),
                        "explicit_rerun": explicit_rerun,
                    },
                    request_id=f"mapping-derive:{uuid4()}",
                )
                generation_id = result.get("generation_record_id")
                if generation_id:
                    st.session_state["mapping_derivation_generation_id"] = str(generation_id)
                st.success(f"Derivation outcome: {result['outcome']}")

        generation_id = st.text_input(
            "Derivation GenerationRecord UUID",
            key="mapping_derivation_generation_id",
            help="The immutable derivation identity used by the material review gate.",
        )
        detail = None
        if generation_id and _valid_uuid(generation_id):
            detail = client.get_mapping_derivation(active_project_id, generation_id)
            st.subheader(f"Derived review set ({len(detail['derived_items'])})")
            for item in detail["derived_items"]:
                with st.container(border=True):
                    st.markdown(
                        f"**{str(item['kind']).replace('_', ' ').title()}** · "
                        f"{'material' if item['material'] else 'non-material'}"
                    )
                    st.caption(
                        f"Target `{item['target_id']}` · {item['materiality_reason']} · "
                        f"evidence {', '.join(str(value) for value in item['evidence_link_ids'])}"
                    )
                    if item["kind"] in {"implementation_requirement", "ambiguity"}:
                        st.page_link(
                            "pages/04_knowledge_review.py",
                            label="Review candidate and evidence",
                            icon=":material/rate_review:",
                        )
                    else:
                        st.page_link(
                            "pages/05_method_review.py",
                            label="Review MethodElement and evidence",
                            icon=":material/account_tree:",
                        )
            st.subheader(f"Discrepancy ledger ({len(detail['discrepancies'])})")
            for discrepancy in detail["discrepancies"]:
                render_discrepancy_card(discrepancy, key=f"discrepancy:{discrepancy['client_id']}")

        st.divider()
        st.subheader("Complete material mapping review gate")
        with st.form("mapping-review-gate"):
            gate_workflow_id = st.text_input("Gate workflow run UUID")
            gate_applicable = st.checkbox("Applicable branch", value=True, key="gate_applicable")
            gate_rationale = st.text_area("Gate rationale", max_chars=4_000)
            gate_submit = st.form_submit_button("Validate complete review set")
        if gate_submit:
            if not _valid_uuid(gate_workflow_id) or not gate_rationale.strip():
                st.error("A valid workflow UUID and human rationale are required.")
            elif gate_applicable and (not generation_id or not _valid_uuid(generation_id)):
                st.error("An exact derivation GenerationRecord is required.")
            else:
                gate = client.complete_mapping_review_gate(
                    active_project_id,
                    {
                        "command_id": str(uuid4()),
                        "workflow_run_id": gate_workflow_id,
                        "derivation_generation_record_id": (
                            generation_id if gate_applicable else None
                        ),
                        "applicable": gate_applicable,
                        "rationale": gate_rationale.strip(),
                    },
                    request_id=f"mapping-gate:{uuid4()}",
                )
                if gate["outcome"] == "blocked":
                    st.error(f"{len(gate['unresolved'])} material review states remain unresolved.")
                    st.json(gate["unresolved"], expanded=False)
                else:
                    st.success(f"Gate outcome: {gate['outcome']}")
                if gate.get("partial_coverage_statement"):
                    st.warning(gate["partial_coverage_statement"])
                st.caption(f"Gate fingerprint: {gate['fingerprint']}")
    except ApiClientError as error:
        st.error(str(error))
