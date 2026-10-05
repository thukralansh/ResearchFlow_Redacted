"""Pure display policy for the bounded four-section MethodModel workspace."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

METHOD_SECTION_PAGE_SIZE: Final = 32
METHOD_AGGREGATE_LIMIT: Final = 128
METHOD_SECTION_ORDER: Final = (
    "objective_mechanism",
    "equations_symbols",
    "algorithm_data_flow",
    "validity_conditions",
)
METHOD_SECTION_LABELS: Final = {
    "objective_mechanism": "Objective and mechanism",
    "equations_symbols": "Equations and symbols",
    "algorithm_data_flow": "Algorithm and data flow",
    "validity_conditions": "Validity conditions",
}


@dataclass(frozen=True, slots=True)
class MethodSectionPage:
    items: tuple[Mapping[str, Any], ...]
    total_items: int
    page_number: int
    page_count: int
    result_fingerprint: str


def section_expanded_by_default(section: str, *, has_error: bool = False) -> bool:
    """Return the approved disclosure default, forcing unresolved errors open."""
    return has_error or section == "objective_mechanism"


def immutable_member_order(member: Mapping[str, Any]) -> tuple[object, ...]:
    """Order every immutable member explicitly; UUID is the final tie-breaker."""
    sequence = member.get("sequence_index")
    element = member["element"]
    return (
        sequence is None,
        0 if sequence is None else int(sequence),
        str(element["element_type"]),
        str(element["lineage_id"]),
        int(element["version"]),
        str(member["id"]),
    )


def group_method_members(
    members: Sequence[Mapping[str, Any]],
) -> dict[str, tuple[Mapping[str, Any], ...]]:
    """Build all four complete immutable section sets without a display cap."""
    grouped: dict[str, list[Mapping[str, Any]]] = {section: [] for section in METHOD_SECTION_ORDER}
    for member in members:
        section = str(member["review_section"])
        if section not in grouped:
            raise ValueError(f"Unknown MethodModel review section: {section}")
        grouped[section].append(member)
    return {
        section: tuple(sorted(grouped[section], key=immutable_member_order))
        for section in METHOD_SECTION_ORDER
    }


def method_section_fingerprint(
    method_model_id: str,
    section: str,
    members: Sequence[Mapping[str, Any]],
) -> str:
    """Bind display pagination to one immutable model, section, and full ordering."""
    payload = {
        "method_model_id": method_model_id,
        "section": section,
        "ordered_members": [
            {
                "member_id": str(member["id"]),
                "element_id": str(member["element"]["id"]),
                "lineage_id": str(member["element"]["lineage_id"]),
                "version": int(member["element"]["version"]),
                "sequence_index": member.get("sequence_index"),
            }
            for member in members
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def paginate_method_section(
    method_model_id: str,
    section: str,
    members: Sequence[Mapping[str, Any]],
    *,
    requested_page: int,
) -> MethodSectionPage:
    """Paginate display only; the complete section remains in the caller's custody."""
    ordered = tuple(sorted(members, key=immutable_member_order))
    fingerprint = method_section_fingerprint(method_model_id, section, ordered)
    page_count = max(1, (len(ordered) + METHOD_SECTION_PAGE_SIZE - 1) // METHOD_SECTION_PAGE_SIZE)
    page_number = min(max(1, requested_page), page_count)
    start = (page_number - 1) * METHOD_SECTION_PAGE_SIZE
    return MethodSectionPage(
        items=ordered[start : start + METHOD_SECTION_PAGE_SIZE],
        total_items=len(ordered),
        page_number=page_number,
        page_count=page_count,
        result_fingerprint=fingerprint,
    )


def element_title(member: Mapping[str, Any]) -> str:
    """Return concise plain text; rendering code decides how to display it safely."""
    payload = member["subtype_payload"]
    element_type = str(member["element"]["element_type"])
    candidate_keys = {
        "component": ("name", "description"),
        "equation": ("label", "latex_expression"),
        "symbol": ("symbol", "canonical_name"),
        "algorithm_step": ("title", "action"),
        "data_flow": ("transformation",),
        "validity_condition": ("statement",),
    }.get(element_type, ())
    for key in candidate_keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return element_type.replace("_", " ").title()
