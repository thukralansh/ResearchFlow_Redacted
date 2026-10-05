"""Persisted value catalogs for the 0001 core schema."""

from enum import StrEnum


class PersistedValue(StrEnum):
    """Base for string values persisted behind named CHECK constraints."""


class ActorType(PersistedValue):
    HUMAN = "human"
    AI = "ai"
    SYSTEM = "system"


class Priority(PersistedValue):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class QuestionType(PersistedValue):
    PRIMARY = "primary"
    SUBQUESTION = "subquestion"
    IMPLEMENTATION = "implementation"
    VERIFICATION = "verification"


class InquiryStatus(PersistedValue):
    OPEN = "open"
    PARTIALLY_ANSWERED = "partially_answered"
    ANSWERED = "answered"


class LifecycleStatus(PersistedValue):
    CURRENT = "current"
    SUPERSEDED = "superseded"


class ReviewStatus(PersistedValue):
    CANDIDATE = "candidate"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class QuestionSourceRelationshipType(PersistedValue):
    PRIMARY_PAPER = "primary_paper"
    SUPPORTING = "supporting"
    BACKGROUND = "background"
    ALTERNATIVE = "alternative"
    CONTRADICTORY = "contradictory"


class SourceType(PersistedValue):
    PDF = "pdf"


class ProcessingStatus(PersistedValue):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RepositorySnapshotStatus(PersistedValue):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class RepositoryRelationshipType(PersistedValue):
    OFFICIAL_IMPLEMENTATION = "official_implementation"
    AUTHOR_AFFILIATED_IMPLEMENTATION = "author_affiliated_implementation"
    OFFICIAL_SUPPORTING_CODE = "official_supporting_code"
    INDEPENDENT_REPRODUCTION = "independent_reproduction"
    COMMUNITY_IMPLEMENTATION = "community_implementation"
    REFERENCE_FRAMEWORK = "reference_framework"
    PARTIAL_IMPLEMENTATION = "partial_implementation"
    RELATED_METHOD = "related_method"
    SUPPORTING_LIBRARY = "supporting_library"
    UNKNOWN = "unknown"


class RepositoryAuthorityLevel(PersistedValue):
    FIRST_PARTY = "first_party"
    AUTHOR_AFFILIATED = "author_affiliated"
    INDEPENDENT = "independent"
    CONTEXTUAL = "contextual"
    UNKNOWN = "unknown"


class CoverageStatus(PersistedValue):
    COMPLETE = "complete"
    PARTIAL = "partial"


class RepositoryAnalysisStage(PersistedValue):
    STAGE_A = "stage_a"
    STAGE_B = "stage_b"


class WorkflowType(PersistedValue):
    FULL_ANALYSIS = "full_analysis"


class WorkflowStatus(PersistedValue):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_REVIEW = "waiting_for_review"
    WAITING_FOR_JUDGMENT = "waiting_for_judgment"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowInterruptStatus(PersistedValue):
    PENDING = "pending"
    RESOLVED = "resolved"
    RESUMED = "resumed"
    CANCELLED = "cancelled"


def values[PersistedValueT: StrEnum](
    value_type: type[PersistedValueT],
) -> tuple[str, ...]:
    """Return stable values for migrations, models, and tests."""
    return tuple(item.value for item in value_type)


def check_values[PersistedValueT: StrEnum](
    column: str,
    value_type: type[PersistedValueT],
) -> str:
    """Build a SQL CHECK expression from a persisted value catalog."""
    allowed = ", ".join(f"'{value}'" for value in values(value_type))
    return f"{column} IN ({allowed})"
