"""Deterministic ordered implementation-plan policy."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Final

from researchflow.errors import DomainValidationError

IMPLEMENTATION_POLICY_VERSION: Final = "rf-implementation-plan-v1"
IMPLEMENTATION_PROMPT_VERSION: Final = "v1"
IMPLEMENTATION_SCHEMA_VERSION: Final = "v1"
IMPLEMENTATION_TASK_NAME: Final = "generate_steps_and_checks"
IMPLEMENTATION_SCHEMA_NAME: Final = "implementation_steps_and_verification_checks"
IMPLEMENTATION_MAX_STEPS: Final = 128
IMPLEMENTATION_MAX_CHECKS: Final = 512
IMPLEMENTATION_TEXT_MAX: Final = 4_000


def canonical_fingerprint(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def validate_ordered_graph(
    steps: Iterable[tuple[str, int, tuple[str, ...]]],
) -> None:
    """Require contiguous order and backward-only dependencies."""
    values = tuple(steps)
    if not values or len(values) > IMPLEMENTATION_MAX_STEPS:
        raise DomainValidationError(details={"reason": "implementation_step_count_invalid"})
    identifiers = [item[0] for item in values]
    if len(set(identifiers)) != len(identifiers):
        raise DomainValidationError(details={"reason": "implementation_step_id_duplicate"})
    if [item[1] for item in values] != list(range(1, len(values) + 1)):
        raise DomainValidationError(details={"reason": "implementation_step_order_invalid"})
    positions = {identifier: index for index, identifier in enumerate(identifiers)}
    for identifier, _, dependencies in values:
        if len(set(dependencies)) != len(dependencies):
            raise DomainValidationError(
                details={"reason": "implementation_step_dependency_duplicate"}
            )
        for dependency in dependencies:
            if dependency not in positions:
                raise DomainValidationError(
                    details={"reason": "implementation_step_dependency_unknown"}
                )
            if positions[dependency] >= positions[identifier]:
                raise DomainValidationError(
                    details={"reason": "implementation_step_dependency_not_prior"}
                )


def validate_requirement_coverage(
    *, required_aliases: frozenset[str], supplied_aliases: Iterable[str]
) -> None:
    supplied = tuple(supplied_aliases)
    if not required_aliases.issubset(supplied):
        raise DomainValidationError(details={"reason": "blocking_requirement_uncovered"})


def validate_human_text(value: str, *, reason: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > IMPLEMENTATION_TEXT_MAX:
        raise DomainValidationError(details={"reason": reason})
    return normalized
