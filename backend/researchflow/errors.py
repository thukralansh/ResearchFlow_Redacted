"""Transport-neutral ResearchFlow errors."""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final

type PublicDetailValue = str | int | float | bool | None

REDACTED: Final = "[REDACTED]"
_SENSITIVE_DETAIL_PARTS: Final = (
    "authorization",
    "content",
    "key",
    "password",
    "payload",
    "secret",
    "source",
    "stack",
    "token",
    "trace",
)


class ErrorCode(StrEnum):
    """Stable machine-readable error codes."""

    CONFIG_INVALID = "config.invalid"
    ACTOR_DEVELOPMENT_FORBIDDEN = "actor.development_forbidden"
    VALIDATION_INVALID = "validation.invalid"
    RESOURCE_NOT_FOUND = "resource.not_found"
    RESOURCE_CONFLICT = "resource.conflict"
    PROVIDER_UNKNOWN_TASK = "provider.unknown_task"
    PROVIDER_INVALID_OUTPUT = "provider.invalid_output"
    PROVIDER_FAILURE = "provider.failure"
    EXTERNAL_FAILURE = "external.failure"
    INTERNAL_ERROR = "internal.error"


def _sanitize_public_details(
    details: Mapping[str, PublicDetailValue] | None,
) -> Mapping[str, PublicDetailValue]:
    if details is None:
        return MappingProxyType({})

    sanitized: dict[str, PublicDetailValue] = {}
    for key, value in details.items():
        normalized_key = key.casefold()
        if any(part in normalized_key for part in _SENSITIVE_DETAIL_PARTS):
            sanitized[key] = REDACTED
        else:
            sanitized[key] = value
    return MappingProxyType(sanitized)


class ResearchFlowError(Exception):
    """Base error with separate public and diagnostic state."""

    def __init__(
        self,
        *,
        code: ErrorCode,
        message: str,
        details: Mapping[str, PublicDetailValue] | None = None,
        cause: Exception | None = None,
    ) -> None:
        self.code = code
        self.public_message = message
        self.public_details = _sanitize_public_details(details)
        super().__init__(message)
        if cause is not None:
            self.__cause__ = cause

    def as_public_dict(self) -> dict[str, object]:
        """Return the only error representation suitable for clients."""
        return {
            "code": self.code.value,
            "message": self.public_message,
            "details": dict(self.public_details),
        }


class ConfigurationError(ResearchFlowError):
    def __init__(
        self,
        *,
        details: Mapping[str, PublicDetailValue] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(
            code=ErrorCode.CONFIG_INVALID,
            message="Runtime configuration is invalid.",
            details=details,
            cause=cause,
        )


class DevelopmentActorForbiddenError(ResearchFlowError):
    def __init__(self) -> None:
        super().__init__(
            code=ErrorCode.ACTOR_DEVELOPMENT_FORBIDDEN,
            message="The development actor is not permitted in external mode.",
        )


class DomainValidationError(ResearchFlowError):
    def __init__(self, *, details: Mapping[str, PublicDetailValue] | None = None) -> None:
        super().__init__(
            code=ErrorCode.VALIDATION_INVALID,
            message="The requested value is invalid.",
            details=details,
        )


class ResourceNotFoundError(ResearchFlowError):
    def __init__(self, *, details: Mapping[str, PublicDetailValue] | None = None) -> None:
        super().__init__(
            code=ErrorCode.RESOURCE_NOT_FOUND,
            message="The requested resource was not found.",
            details=details,
        )


class ResourceConflictError(ResearchFlowError):
    def __init__(self, *, details: Mapping[str, PublicDetailValue] | None = None) -> None:
        super().__init__(
            code=ErrorCode.RESOURCE_CONFLICT,
            message="The requested operation conflicts with current state.",
            details=details,
        )


class UnknownProviderTaskError(ResearchFlowError):
    def __init__(self, *, details: Mapping[str, PublicDetailValue] | None = None) -> None:
        super().__init__(
            code=ErrorCode.PROVIDER_UNKNOWN_TASK,
            message="The provider does not have a fixture for the requested task.",
            details=details,
        )


class InvalidProviderOutputError(ResearchFlowError):
    def __init__(
        self,
        *,
        details: Mapping[str, PublicDetailValue] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(
            code=ErrorCode.PROVIDER_INVALID_OUTPUT,
            message="The provider returned output that failed validation.",
            details=details,
            cause=cause,
        )


class ProviderFailureError(ResearchFlowError):
    def __init__(
        self,
        *,
        details: Mapping[str, PublicDetailValue] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(
            code=ErrorCode.PROVIDER_FAILURE,
            message="The configured provider failed.",
            details=details,
            cause=cause,
        )


class ExternalServiceError(ResearchFlowError):
    def __init__(
        self,
        *,
        details: Mapping[str, PublicDetailValue] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(
            code=ErrorCode.EXTERNAL_FAILURE,
            message="An external service is unavailable.",
            details=details,
            cause=cause,
        )


class InternalError(ResearchFlowError):
    def __init__(
        self,
        *,
        details: Mapping[str, PublicDetailValue] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(
            code=ErrorCode.INTERNAL_ERROR,
            message="An unexpected internal error occurred.",
            details=details,
            cause=cause,
        )
