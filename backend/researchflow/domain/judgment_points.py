""" evidence resolvability and request completeness policy."""

from dataclasses import dataclass
from typing import Final

from researchflow.domain.blueprint_catalog import DependencyCriticality

JUDGMENT_DRAFT_TASK_NAME: Final = "draft_judgment_request"
JUDGMENT_DRAFT_PROMPT_VERSION: Final = "v1"
JUDGMENT_DRAFT_SCHEMA_NAME: Final = "judgment_request_draft"
JUDGMENT_DRAFT_SCHEMA_VERSION: Final = "v1"
JUDGMENT_POINT_DETECTION_PROMPT_VERSION: Final = "v1"
MAX_JUDGMENT_TEXT: Final = 4_000
MAX_JUDGMENT_OPTIONS: Final = 5


@dataclass(frozen=True, slots=True)
class EvidenceResolvability:
    resolvable: bool
    rationale: str


@dataclass(frozen=True, slots=True)
class EvidenceState:
    review_status: str
    lifecycle_status: str
    evidence_relation: str


def accepted_current_evidence(
    evidence: tuple[EvidenceState, ...],
) -> tuple[EvidenceState, ...]:
    return tuple(
        item
        for item in evidence
        if item.review_status == "accepted" and item.lifecycle_status == "current"
    )


def assess_evidence_resolvability(
    *, evidence: tuple[EvidenceState, ...], interpretation_count: int
) -> EvidenceResolvability:
    """Return whether evidence can support resolution; never perform that resolution."""
    eligible = accepted_current_evidence(evidence)
    support = sum(item.evidence_relation == "supports" for item in eligible)
    contradiction = sum(item.evidence_relation == "contradicts" for item in eligible)
    qualification = sum(item.evidence_relation == "qualifies" for item in eligible)
    if support == 0:
        return EvidenceResolvability(False, "No accepted/current supporting evidence exists.")
    if contradiction:
        return EvidenceResolvability(
            False, "Accepted/current support and contradiction require adjudication."
        )
    if qualification:
        return EvidenceResolvability(
            False, "Accepted/current material qualification prevents an unqualified resolution."
        )
    if interpretation_count != 1:
        return EvidenceResolvability(
            False,
            "Accepted/current evidence does not establish one uniquely supported interpretation.",
        )
    return EvidenceResolvability(
        True,
        "Accepted/current evidence supports one interpretation without contradiction or "
        "material qualification; resolution must still use the normal lifecycle service.",
    )


def needs_judgment(*, criticalities: tuple[DependencyCriticality, ...], resolvable: bool) -> bool:
    return (
        any(
            value in {DependencyCriticality.BLOCKING, DependencyCriticality.MATERIAL}
            for value in criticalities
        )
        and not resolvable
    )
