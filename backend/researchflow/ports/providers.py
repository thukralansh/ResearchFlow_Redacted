"""Provider interfaces and structured transport records."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from researchflow.errors import DomainValidationError

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


def _require_nonempty(value: str, field_name: str) -> None:
    if not value.strip():
        raise DomainValidationError(details={"field": field_name})


@dataclass(frozen=True, slots=True)
class StructuredGenerationMessage:
    """One bounded provider-neutral message."""

    role: str
    content: str

    def __post_init__(self) -> None:
        if self.role not in {"system", "user", "assistant"}:
            raise DomainValidationError(details={"field": "message.role"})
        _require_nonempty(self.content, "message.content")


@dataclass(frozen=True, slots=True)
class StructuredGenerationModelPolicy:
    """Expected provider/model behavior, independent of any SDK."""

    provider: str
    model_version: str
    policy_version: str
    reasoning_version: str | None
    temperature: float
    configuration: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_nonempty(self.provider, "provider")
        _require_nonempty(self.model_version, "model_version")
        _require_nonempty(self.policy_version, "policy_version")
        if self.reasoning_version is not None:
            _require_nonempty(self.reasoning_version, "reasoning_version")
        if not 0 <= self.temperature <= 2:
            raise DomainValidationError(details={"field": "temperature"})


@dataclass(frozen=True, slots=True)
class StructuredGenerationRequest:
    """Validated input to a structured generation provider."""

    task_name: str
    input_fingerprint: str
    schema_name: str
    schema_version: str
    schema_hash: str
    json_schema: Mapping[str, JsonValue]
    prompt_version: str
    prompt_hash: str
    prompt: str
    messages: tuple[StructuredGenerationMessage, ...]
    model_policy: StructuredGenerationModelPolicy

    def __post_init__(self) -> None:
        _require_nonempty(self.task_name, "task_name")
        _require_nonempty(self.input_fingerprint, "input_fingerprint")
        _require_nonempty(self.schema_name, "schema_name")
        _require_nonempty(self.schema_version, "schema_version")
        _require_nonempty(self.schema_hash, "schema_hash")
        _require_nonempty(self.prompt_version, "prompt_version")
        _require_nonempty(self.prompt_hash, "prompt_hash")
        _require_nonempty(self.prompt, "prompt")


@dataclass(frozen=True, slots=True)
class StructuredGenerationResult:
    """Provider output before application-layer persistence."""

    task_name: str
    input_fingerprint: str
    schema_name: str
    provider: str
    model_version: str
    reasoning_version: str | None
    model_configuration: Mapping[str, JsonValue]
    payload: Mapping[str, JsonValue]
    output_fingerprint: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None

    def canonical_bytes(self) -> bytes:
        """Serialize deterministically for fixture and replay comparison."""
        value = {
            "input_fingerprint": self.input_fingerprint,
            "input_tokens": self.input_tokens,
            "latency_ms": self.latency_ms,
            "model_configuration": self.model_configuration,
            "model_version": self.model_version,
            "output_fingerprint": self.output_fingerprint,
            "output_tokens": self.output_tokens,
            "payload": self.payload,
            "provider": self.provider,
            "reasoning_version": self.reasoning_version,
            "schema_name": self.schema_name,
            "task_name": self.task_name,
        }
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    """Reserved embedding result; vector retrieval is not active."""

    model: str
    vectors: tuple[tuple[float, ...], ...]


@runtime_checkable
class StructuredGenerationProvider(Protocol):
    """Structured generation without persistence access."""

    async def generate(
        self,
        request: StructuredGenerationRequest,
    ) -> StructuredGenerationResult: ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Reserved provider boundary for a future measured activation gate."""

    async def embed(self, texts: Sequence[str]) -> EmbeddingResult: ...
