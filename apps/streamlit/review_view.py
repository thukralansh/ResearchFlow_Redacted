"""Pure display policy for the proposition review workspace."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

REVIEW_PAGE_SIZE: Final = 32


@dataclass(frozen=True, slots=True)
class ReviewFilters:
    object_types: frozenset[str] = frozenset()
    review_statuses: frozenset[str] = frozenset()
    generation_record_ids: frozenset[str] = frozenset()
    search_text: str = ""


@dataclass(frozen=True, slots=True)
class ReviewPage:
    items: tuple[Mapping[str, Any], ...]
    total_items: int
    page_number: int
    page_count: int
    result_fingerprint: str


def immutable_review_order(item: Mapping[str, Any]) -> tuple[str, str, str, str, int, str]:
    """Order only by immutable fields, ending in the object UUID tie-breaker."""
    return (
        str(item.get("generation_record_id") or ""),
        str(item["object_type"]),
        str(item["normalized_statement_hash"]),
        str(item["lineage_id"]),
        int(item["version"]),
        str(item["id"]),
    )


def filter_review_items(
    items: Sequence[Mapping[str, Any]], filters: ReviewFilters
) -> tuple[Mapping[str, Any], ...]:
    """Filter then deterministically order the complete server result."""
    search = filters.search_text.strip().casefold()
    selected = (
        item
        for item in items
        if (not filters.object_types or str(item["object_type"]) in filters.object_types)
        and (not filters.review_statuses or str(item["review_status"]) in filters.review_statuses)
        and (
            not filters.generation_record_ids
            or str(item.get("generation_record_id")) in filters.generation_record_ids
        )
        and (
            not search
            or search in str(item["statement"]).casefold()
            or search in str(item["object_type"]).casefold()
        )
    )
    return tuple(sorted(selected, key=immutable_review_order))


def result_fingerprint(ordered_items: Sequence[Mapping[str, Any]], filters: ReviewFilters) -> str:
    """Bind display pagination to the full ordered identity and active filters."""
    payload = {
        "policy": "rf-proposition-review-pagination-v1",
        "object_types": sorted(filters.object_types),
        "review_statuses": sorted(filters.review_statuses),
        "generation_record_ids": sorted(filters.generation_record_ids),
        "search_text": filters.search_text.strip(),
        "ordered_ids": [str(item["id"]) for item in ordered_items],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def paginate_review_items(
    ordered_items: Sequence[Mapping[str, Any]],
    *,
    requested_page: int,
    fingerprint: str,
) -> ReviewPage:
    """Return a display-only page while clamping stale page selections."""
    total = len(ordered_items)
    page_count = max(1, math.ceil(total / REVIEW_PAGE_SIZE))
    page_number = min(max(requested_page, 1), page_count)
    start = (page_number - 1) * REVIEW_PAGE_SIZE
    return ReviewPage(
        items=tuple(ordered_items[start : start + REVIEW_PAGE_SIZE]),
        total_items=total,
        page_number=page_number,
        page_count=page_count,
        result_fingerprint=fingerprint,
    )


def review_status_counts(items: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Return descriptive version counts only; this is never review-gate authority."""
    counts = Counter(str(item["review_status"]) for item in items)
    return {
        status: counts.get(status, 0)
        for status in ("candidate", "needs_revision", "accepted", "rejected")
    }


def review_lineage_count(items: Sequence[Mapping[str, Any]]) -> int:
    """Count distinct immutable semantic lineages represented by the version rows."""
    return len({str(item["lineage_id"]) for item in items})
