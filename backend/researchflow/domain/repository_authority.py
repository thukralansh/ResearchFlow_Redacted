"""Public GitHub identity and human-confirmed repository authority policy."""

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Final
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from researchflow.errors import DomainValidationError, ResourceConflictError

GITHUB_HOST: Final = "github.com"
GIT_SHA1 = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY_PART = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
RATIONALE_MAX: Final = 4_000
EVIDENCE_TEXT_MAX: Final = 4_000
REF_MAX: Final = 255

RELATIONSHIP_TYPES: Final = frozenset(
    {
        "official_implementation",
        "author_affiliated_implementation",
        "official_supporting_code",
        "independent_reproduction",
        "community_implementation",
        "reference_framework",
        "partial_implementation",
        "related_method",
        "supporting_library",
        "unknown",
    }
)
AUTHORITY_LEVELS: Final = frozenset(
    {"first_party", "author_affiliated", "independent", "contextual", "unknown"}
)

REPOSITORY_ID_NAMESPACE: Final = uuid5(NAMESPACE_URL, "https://researchflow.local/ids/repository")
SNAPSHOT_ID_NAMESPACE: Final = uuid5(
    NAMESPACE_URL, "https://researchflow.local/ids/repository-snapshot"
)
REPOSITORY_RELATIONSHIP_ID_NAMESPACE: Final = uuid5(
    NAMESPACE_URL, "https://researchflow.local/ids/paper-repository-relationship"
)
REPOSITORY_EVENT_ID_NAMESPACE: Final = uuid5(
    NAMESPACE_URL, "https://researchflow.local/ids/repository-operation-event"
)


@dataclass(frozen=True, slots=True)
class NormalizedGitHubRepository:
    owner: str
    name: str
    url: str


@dataclass(frozen=True, slots=True)
class ConfirmedRepositoryAuthority:
    relationship_type: str
    authority_level: str
    rationale: str
    evidence_text: str | None
    confidence: Decimal | None


@dataclass(frozen=True, slots=True)
class RepositoryAttachIdentities:
    repository_id: UUID
    snapshot_id: UUID
    relationship_id: UUID
    event_id: UUID


def normalize_github_url(repository_url: str) -> NormalizedGitHubRepository:
    candidate = repository_url.strip()
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as error:
        raise DomainValidationError(details={"reason": "github_url_invalid"}) from error
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.hostname.casefold() != GITHUB_HOST
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise DomainValidationError(details={"reason": "github_url_not_allowed"})
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2:
        raise DomainValidationError(details={"reason": "github_repository_path_invalid"})
    owner, name = parts
    if name.casefold().endswith(".git"):
        name = name[:-4]
    if not REPOSITORY_PART.fullmatch(owner) or not REPOSITORY_PART.fullmatch(name):
        raise DomainValidationError(details={"reason": "github_repository_identity_invalid"})
    owner = owner.casefold()
    name = name.casefold()
    return NormalizedGitHubRepository(owner, name, f"https://github.com/{owner}/{name}")


def validate_requested_ref(requested_ref: str | None) -> str | None:
    if requested_ref is None:
        return None
    normalized = requested_ref.strip()
    if (
        not normalized
        or len(normalized) > REF_MAX
        or normalized.startswith("-")
        or normalized.startswith("/")
        or normalized.endswith(("/", "."))
        or ".." in normalized
        or "//" in normalized
        or "@{" in normalized
        or normalized == "@"
        or any(
            character.isspace() or ord(character) < 32 or character in "~^:?*[\\"
            for character in normalized
        )
        or any(
            component.startswith(".") or component.casefold().endswith(".lock")
            for component in normalized.split("/")
        )
    ):
        raise DomainValidationError(details={"reason": "github_ref_invalid"})
    return normalized


def validate_commit_sha(commit_sha: str) -> str:
    normalized = commit_sha.strip().casefold()
    if GIT_SHA1.fullmatch(normalized) is None:
        raise DomainValidationError(details={"reason": "github_commit_sha_invalid"})
    return normalized


def confirm_repository_authority(
    *,
    relationship_type: str,
    authority_level: str,
    rationale: str,
    evidence_text: str | None,
    confidence: Decimal | None,
    human_confirmed: bool,
) -> ConfirmedRepositoryAuthority:
    if not human_confirmed:
        raise ResourceConflictError(details={"reason": "repository_authority_not_confirmed"})
    relationship_type = relationship_type.strip()
    authority_level = authority_level.strip()
    if relationship_type not in RELATIONSHIP_TYPES:
        raise DomainValidationError(details={"reason": "repository_relationship_type_invalid"})
    if authority_level not in AUTHORITY_LEVELS:
        raise DomainValidationError(details={"reason": "repository_authority_level_invalid"})
    if relationship_type == "official_implementation" and authority_level != "first_party":
        raise DomainValidationError(details={"reason": "repository_role_authority_incompatible"})
    rationale = rationale.strip()
    evidence = evidence_text.strip() if evidence_text is not None else None
    if not rationale or len(rationale) > RATIONALE_MAX:
        raise DomainValidationError(details={"reason": "repository_rationale_invalid"})
    if evidence is not None and (not evidence or len(evidence) > EVIDENCE_TEXT_MAX):
        raise DomainValidationError(details={"reason": "repository_evidence_text_invalid"})
    if confidence is not None and not Decimal("0") <= confidence <= Decimal("1"):
        raise DomainValidationError(details={"reason": "repository_confidence_invalid"})
    return ConfirmedRepositoryAuthority(
        relationship_type, authority_level, rationale, evidence, confidence
    )


def repository_attach_fingerprint(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def repository_attach_identities(
    *,
    project_id: UUID,
    normalized_url: str,
    commit_sha: str,
    idempotency_key: str | None,
    fingerprint: str,
) -> RepositoryAttachIdentities:
    repository_id = uuid5(REPOSITORY_ID_NAMESPACE, f"{project_id}:{normalized_url}")
    snapshot_id = uuid5(SNAPSHOT_ID_NAMESPACE, f"{repository_id}:{commit_sha}")
    command_identity = idempotency_key or str(uuid4())
    command = f"{project_id}:{command_identity}:{fingerprint}"
    return RepositoryAttachIdentities(
        repository_id=repository_id,
        snapshot_id=snapshot_id,
        relationship_id=uuid5(REPOSITORY_RELATIONSHIP_ID_NAMESPACE, command),
        event_id=uuid5(REPOSITORY_EVENT_ID_NAMESPACE, command),
    )
