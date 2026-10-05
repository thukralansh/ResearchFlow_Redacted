"""Project input policy and stable command identities."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from researchflow.errors import DomainValidationError

PROJECT_TITLE_MAX: Final = 200
PROJECT_DESCRIPTION_MAX: Final = 8_000
TARGET_OUTCOME_MAX: Final = 2_000
REPRODUCTION_DEPTH_MAX: Final = 200
IDEMPOTENCY_KEY_MAX: Final = 200

PROJECT_ID_NAMESPACE: Final = uuid5(NAMESPACE_URL, "https://researchflow.local/ids/project")
PRIMARY_QUESTION_ID_NAMESPACE: Final = uuid5(
    NAMESPACE_URL, "https://researchflow.local/ids/primary-research-question"
)
OPERATION_EVENT_ID_NAMESPACE: Final = uuid5(
    NAMESPACE_URL, "https://researchflow.local/ids/operation-event"
)


def _required(value: str, *, field: str, maximum: int) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise DomainValidationError(details={"field": field, "reason": "invalid_length"})
    return normalized


def _optional(value: str | None, *, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > maximum:
        raise DomainValidationError(details={"field": field, "reason": "invalid_length"})
    return normalized


@dataclass(frozen=True, slots=True)
class ProjectDefinition:
    """Validated canonical Project configuration for the first slice."""

    title: str
    description: str | None
    target_outcome: str
    reproduction_depth: str

    @classmethod
    def validate(
        cls,
        *,
        title: str,
        description: str | None,
        target_outcome: str,
        reproduction_depth: str,
    ) -> "ProjectDefinition":
        return cls(
            title=_required(title, field="title", maximum=PROJECT_TITLE_MAX),
            description=_optional(
                description,
                field="description",
                maximum=PROJECT_DESCRIPTION_MAX,
            ),
            target_outcome=_required(
                target_outcome,
                field="target_outcome",
                maximum=TARGET_OUTCOME_MAX,
            ),
            reproduction_depth=_required(
                reproduction_depth,
                field="reproduction_depth",
                maximum=REPRODUCTION_DEPTH_MAX,
            ),
        )


@dataclass(frozen=True, slots=True)
class ProjectCreateIdentities:
    project_id: UUID
    primary_question_id: UUID
    event_id: UUID


def normalize_idempotency_key(key: str | None) -> str | None:
    if key is None:
        return None
    return _required(key, field="idempotency_key", maximum=IDEMPOTENCY_KEY_MAX)


def project_create_identities(
    *, actor_id: UUID, idempotency_key: str | None
) -> ProjectCreateIdentities:
    """Use distinct namespaces for a replayable create command's record IDs."""
    if idempotency_key is None:
        return ProjectCreateIdentities(uuid4(), uuid4(), uuid4())

    command_name = f"{actor_id}:project.create:{idempotency_key}"
    return ProjectCreateIdentities(
        project_id=uuid5(PROJECT_ID_NAMESPACE, command_name),
        primary_question_id=uuid5(PRIMARY_QUESTION_ID_NAMESPACE, command_name),
        event_id=uuid5(OPERATION_EVENT_ID_NAMESPACE, command_name),
    )


def command_fingerprint(payload: Mapping[str, object]) -> str:
    """Hash canonical, content-bearing command input for safe replay checks."""
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
