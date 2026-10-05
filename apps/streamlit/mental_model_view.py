"""Pure view helpers for read-only Mental Model inspection."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

ROLE_ORDER = (
    "core_claim",
    "mechanism",
    "definition",
    "assumption",
    "validity_condition",
    "limitation",
    "knowledge_gap",
    "implementation_consequence",
    "human_judgment",
    "decisive_supporting_evidence",
    "central_contradicting_evidence",
)


def group_members_by_role(
    members: Sequence[Mapping[str, Any]],
) -> tuple[tuple[str, tuple[Mapping[str, Any], ...]], ...]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for item in members:
        grouped[str(item["member"]["role"])].append(item)
    ordered_roles = [role for role in ROLE_ORDER if role in grouped]
    ordered_roles.extend(sorted(set(grouped).difference(ordered_roles)))
    return tuple(
        (
            role,
            tuple(
                sorted(
                    grouped[role],
                    key=lambda item: (
                        str(item["target_type"]),
                        str(item["member"]["id"]),
                    ),
                )
            ),
        )
        for role in ordered_roles
    )


def member_title(item: Mapping[str, Any]) -> str:
    target = item["target"]
    if item["target_type"] == "knowledge_object":
        return str(target["statement"])
    if item["target_type"] == "human_judgment":
        return str(target.get("custom_decision") or target.get("selected_option_id") or "Judgment")
    evidence = item.get("evidence", ())
    return str(evidence[0]["excerpt"] if evidence else "Exceptional evidence")


def latest_rendering(
    renderings: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    return renderings[-1] if renderings else None


def status_label(model: Mapping[str, Any], *, is_current: bool) -> str:
    status = str(model["status"])
    if is_current and status == "current":
        return "Current"
    if status == "current":
        return "Historical (not current)"
    return status.replace("_", " ").title()


def graph_rows(detail: Mapping[str, Any]) -> tuple[tuple[str, str, str], ...]:
    """Return stable source/target/edge rows; graph layout is never persisted."""
    model_id = str(detail["model"]["id"])
    rows = [
        (str(detail["question"]["id"]), model_id, "frames"),
        (str(detail["method_model"]["id"]), model_id, "method"),
    ]
    rows.extend(
        (model_id, str(item["member"]["id"]), str(item["member"]["role"]))
        for item in detail["members"]
    )
    return tuple(rows)
