"""Provider-neutral structured generation with append-only attempt history."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Final
from uuid import UUID, uuid4

from pydantic import BaseModel, ValidationError

from researchflow.errors import (
    DomainValidationError,
    InternalError,
    InvalidProviderOutputError,
    ProviderFailureError,
    ResearchFlowError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from researchflow.ports.providers import (
    JsonValue,
    StructuredGenerationMessage,
    StructuredGenerationModelPolicy,
    StructuredGenerationProvider,
    StructuredGenerationRequest,
    StructuredGenerationResult,
)
from researchflow.ports.repositories import GenerationRecordData, NewGenerationRecord
from researchflow.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory
from researchflow.prompts.registry import PromptBinding, PromptRegistry

DEFAULT_MAX_CONTEXT_BYTES: Final = 1_048_576
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class GenerateStructuredCommand[OutputT: BaseModel]:
    project_id: UUID
    workflow_run_id: UUID | None
    task_name: str
    input_fingerprint: str
    prompt_version: str
    schema_name: str
    schema_version: str
    output_type: type[OutputT]
    messages: tuple[StructuredGenerationMessage, ...]
    model_policy: StructuredGenerationModelPolicy
    explicit_rerun: bool = False
    provider_json_schema: dict[str, JsonValue] | None = None
    schema_variant_policy_version: str | None = None
    defer_application_domain_validation: bool = False
    copilot_turn_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class StructuredGenerationOutcome[OutputT: BaseModel]:
    value: OutputT
    generation_record: GenerationRecordData
    replayed: bool


class StructuredGenerationService:
    """Call a provider outside transactions and persist every completed outcome."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        provider: StructuredGenerationProvider,
        prompt_registry: PromptRegistry,
        max_context_bytes: int = DEFAULT_MAX_CONTEXT_BYTES,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if max_context_bytes <= 0:
            raise DomainValidationError(details={"field": "max_context_bytes"})
        self._uow_factory = uow_factory
        self._provider = provider
        self._prompt_registry = prompt_registry
        self._max_context_bytes = max_context_bytes
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory

    async def generate_structured[OutputT: BaseModel](
        self, command: GenerateStructuredCommand[OutputT]
    ) -> StructuredGenerationOutcome[OutputT]:
        base_binding = self._prompt_registry.load(
            task_name=command.task_name,
            prompt_version=command.prompt_version,
            schema_name=command.schema_name,
            schema_version=command.schema_version,
            output_type=command.output_type,
        )
        binding = _effective_binding(command, base_binding)
        self._validate_command(command, binding)
        provider_configuration = _provider_configuration(command.model_policy)
        logical_key = _logical_key(command, binding, provider_configuration)
        persisted_configuration = {
            **provider_configuration,
            "generation_logical_key": logical_key,
            "prompt_hash": binding.prompt_hash,
            "schema_hash": binding.schema_hash,
            "schema_name": binding.schema_name,
        }
        if command.provider_json_schema is not None:
            persisted_configuration.update(
                {
                    "base_schema_hash": base_binding.schema_hash,
                    "schema_variant_policy_version": command.schema_variant_policy_version,
                }
            )
        if command.defer_application_domain_validation:
            persisted_configuration["application_domain_validation_deferred"] = True

        async with self._uow_factory(command.project_id) as uow:
            await _validate_execution_owner(uow, command)
            version_records = await uow.generations.list_prompt_version_records(
                task_name=command.task_name,
                prompt_version=binding.prompt_version,
            )
            _validate_persisted_binding(version_records, base_binding)
            existing = None
            if not command.explicit_rerun:
                if command.workflow_run_id is not None:
                    existing = await uow.generations.find_reusable_success(
                        workflow_run_id=command.workflow_run_id,
                        task_name=command.task_name,
                        prompt_version=binding.prompt_version,
                        schema_version=binding.schema_version,
                        provider=command.model_policy.provider,
                        model_version=command.model_policy.model_version,
                        reasoning_version=command.model_policy.reasoning_version,
                        input_fingerprint=command.input_fingerprint,
                        model_configuration=persisted_configuration,
                    )
                elif command.copilot_turn_id is not None:
                    existing = await uow.generations.find_reusable_copilot_success(
                        copilot_turn_id=command.copilot_turn_id,
                        task_name=command.task_name,
                        prompt_version=binding.prompt_version,
                        schema_version=binding.schema_version,
                        provider=command.model_policy.provider,
                        model_version=command.model_policy.model_version,
                        reasoning_version=command.model_policy.reasoning_version,
                        input_fingerprint=command.input_fingerprint,
                        model_configuration=persisted_configuration,
                    )
            await uow.rollback()
        if existing is not None:
            value = _validate_replayed(
                command.output_type,
                existing,
                defer_application_domain_validation=(command.defer_application_domain_validation),
            )
            return StructuredGenerationOutcome(
                value=value,
                generation_record=existing,
                replayed=True,
            )

        request = StructuredGenerationRequest(
            task_name=command.task_name,
            input_fingerprint=command.input_fingerprint,
            schema_name=binding.schema_name,
            schema_version=binding.schema_version,
            schema_hash=binding.schema_hash,
            json_schema=binding.json_schema,
            prompt_version=binding.prompt_version,
            prompt_hash=binding.prompt_hash,
            prompt=binding.content,
            messages=command.messages,
            model_policy=command.model_policy,
        )
        started_at = _as_utc(self._clock())
        result: StructuredGenerationResult | None = None
        failure: ResearchFlowError | None = None
        validation_summary: dict[str, object] | list[object] | None = None
        validated_output: OutputT | None = None
        try:
            result = await self._provider.generate(request)
            _validate_result_identity(result, command, provider_configuration)
            validated_output = command.output_type.model_validate_json(
                json.dumps(result.payload, sort_keys=True, separators=(",", ":")),
                strict=True,
                context={
                    "defer_application_domain_validation": (
                        command.defer_application_domain_validation
                    )
                },
            )
        except ValidationError as error:
            validation_summary = _validation_summary(error)
            failure = InvalidProviderOutputError(details={"reason": "schema_validation_failed"})
        except ResearchFlowError as error:
            failure = error
            validation_summary = _failure_summary(error)
        except Exception as error:
            failure = ProviderFailureError(details={"reason": "adapter_exception"}, cause=error)
            validation_summary = {"reason": "adapter_exception"}
        completed_at = _as_utc(self._clock())
        record_configuration = {
            **persisted_configuration,
            "raw_output_fingerprint": None if result is None else result.output_fingerprint,
            "provider_reported_model": (
                None
                if result is None
                else result.model_configuration.get("provider_reported_model")
            ),
            "provider_schema_compiler_version": _attempt_metadata(
                result, failure, "provider_schema_compiler_version"
            ),
            "provider_schema_hash": _attempt_metadata(result, failure, "provider_schema_hash"),
            "estimated_cost_usd": _estimated_cost_usd(
                result,
                command.model_policy.configuration,
            ),
        }

        record = NewGenerationRecord(
            id=self._id_factory(),
            project_id=command.project_id,
            workflow_run_id=command.workflow_run_id,
            copilot_turn_id=command.copilot_turn_id,
            task_name=command.task_name,
            prompt_version=binding.prompt_version,
            schema_version=binding.schema_version,
            provider=command.model_policy.provider,
            model_version=command.model_policy.model_version,
            reasoning_version=command.model_policy.reasoning_version,
            input_fingerprint=command.input_fingerprint,
            status="failed" if failure is not None else "succeeded",
            model_configuration=record_configuration,
            input_tokens=None if result is None else result.input_tokens,
            output_tokens=None if result is None else result.output_tokens,
            latency_ms=_latency_ms(result, started_at, completed_at),
            raw_structured_output=_safe_raw_output(result),
            validation_summary=validation_summary,
            error_code=None if failure is None else failure.code.value,
            started_at=started_at,
            completed_at=completed_at,
        )
        async with self._uow_factory(command.project_id) as uow:
            await _validate_execution_owner(uow, command)
            persisted = await uow.generations.add(record)
            await uow.commit()

        if failure is not None:
            raise failure
        if validated_output is None:
            raise InternalError(details={"reason": "generation_value_missing"})
        return StructuredGenerationOutcome(
            value=validated_output,
            generation_record=persisted,
            replayed=False,
        )

    def _validate_command[OutputT: BaseModel](
        self,
        command: GenerateStructuredCommand[OutputT],
        binding: PromptBinding,
    ) -> None:
        if (command.workflow_run_id is None) == (command.copilot_turn_id is None):
            raise DomainValidationError(details={"reason": "exactly_one_execution_owner_required"})
        if _SHA256.fullmatch(command.input_fingerprint) is None:
            raise DomainValidationError(details={"field": "input_fingerprint"})
        total_bytes = len(binding.content.encode("utf-8")) + len(
            json.dumps(binding.json_schema, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        total_bytes += sum(len(message.content.encode("utf-8")) for message in command.messages)
        if total_bytes > self._max_context_bytes:
            raise DomainValidationError(
                details={"field": "messages", "reason": "context_too_large"}
            )


def _provider_configuration(policy: StructuredGenerationModelPolicy) -> dict[str, object]:
    return {
        "policy_version": policy.policy_version,
        "temperature": policy.temperature,
        "provider_configuration": dict(policy.configuration),
    }


async def _validate_execution_owner[OutputT: BaseModel](
    uow: UnitOfWork, command: GenerateStructuredCommand[OutputT]
) -> None:
    if command.workflow_run_id is not None:
        if await uow.workflows.get_exact(command.workflow_run_id) is None:
            raise ResourceNotFoundError(details={"resource": "workflow_run"})
        return
    if (
        command.copilot_turn_id is None
        or await uow.copilot.get_turn(command.copilot_turn_id) is None
    ):
        raise ResourceNotFoundError(details={"resource": "copilot_turn"})


def _effective_binding[OutputT: BaseModel](
    command: GenerateStructuredCommand[OutputT], base: PromptBinding
) -> PromptBinding:
    override = command.provider_json_schema
    policy = command.schema_variant_policy_version
    if (override is None) != (policy is None):
        raise DomainValidationError(details={"reason": "schema_variant_identity_incomplete"})
    if override is None:
        return base
    if not policy or not policy.strip() or not override:
        raise DomainValidationError(details={"reason": "schema_variant_invalid"})
    raw = json.dumps(override, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return replace(
        base,
        schema_hash=hashlib.sha256(raw).hexdigest(),
        json_schema=override,
    )


def _validate_persisted_binding(
    records: tuple[GenerationRecordData, ...], binding: PromptBinding
) -> None:
    expected = (
        binding.prompt_hash,
        binding.schema_name,
        binding.schema_version,
        binding.schema_hash,
    )
    for record in records:
        actual = (
            record.model_configuration.get("prompt_hash"),
            record.model_configuration.get("schema_name"),
            record.schema_version,
            record.model_configuration.get(
                "base_schema_hash", record.model_configuration.get("schema_hash")
            ),
        )
        if actual != expected:
            raise ResourceConflictError(details={"reason": "persisted_prompt_binding_changed"})


def _logical_key[OutputT: BaseModel](
    command: GenerateStructuredCommand[OutputT],
    binding: PromptBinding,
    provider_configuration: Mapping[str, object],
) -> str:
    identity = {
        "input_fingerprint": command.input_fingerprint,
        "model_version": command.model_policy.model_version,
        "prompt_hash": binding.prompt_hash,
        "prompt_version": binding.prompt_version,
        "provider": command.model_policy.provider,
        "provider_configuration": provider_configuration,
        "reasoning_version": command.model_policy.reasoning_version,
        "schema_hash": binding.schema_hash,
        "schema_name": binding.schema_name,
        "schema_version": binding.schema_version,
        "schema_variant_policy_version": command.schema_variant_policy_version,
        "task_name": command.task_name,
        "application_domain_validation_deferred": (command.defer_application_domain_validation),
    }
    raw = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _validate_result_identity[OutputT: BaseModel](
    result: StructuredGenerationResult,
    command: GenerateStructuredCommand[OutputT],
    expected_configuration: Mapping[str, object],
) -> None:
    result_configuration = dict(result.model_configuration)
    provider_reported_model = result_configuration.pop("provider_reported_model", None)
    provider_schema_compiler_version = result_configuration.pop(
        "provider_schema_compiler_version", None
    )
    provider_schema_hash = result_configuration.pop("provider_schema_hash", None)
    if result.provider == "openai":
        if not isinstance(provider_reported_model, str) or not provider_reported_model:
            raise InvalidProviderOutputError(details={"reason": "provider_metadata_mismatch"})
        provider_configuration = expected_configuration.get("provider_configuration")
        expected_compiler_version = (
            provider_configuration.get("schema_compiler_version")
            if isinstance(provider_configuration, Mapping)
            else None
        )
        if (
            provider_schema_compiler_version != expected_compiler_version
            or not isinstance(provider_schema_hash, str)
            or _SHA256.fullmatch(provider_schema_hash) is None
        ):
            raise InvalidProviderOutputError(details={"reason": "provider_metadata_mismatch"})
    elif provider_reported_model is not None:
        raise InvalidProviderOutputError(details={"reason": "provider_metadata_mismatch"})
    elif provider_schema_compiler_version is not None or provider_schema_hash is not None:
        raise InvalidProviderOutputError(details={"reason": "provider_metadata_mismatch"})
    actual = (
        result.task_name,
        result.input_fingerprint,
        result.schema_name,
        result.provider,
        result.model_version,
        result.reasoning_version,
        result_configuration,
    )
    expected = (
        command.task_name,
        command.input_fingerprint,
        command.schema_name,
        command.model_policy.provider,
        command.model_policy.model_version,
        command.model_policy.reasoning_version,
        dict(expected_configuration),
    )
    if actual != expected:
        raise InvalidProviderOutputError(details={"reason": "provider_metadata_mismatch"})
    if result.output_fingerprint != _raw_output_fingerprint(result.payload):
        raise InvalidProviderOutputError(details={"reason": "output_fingerprint_mismatch"})
    for value, field in (
        (result.input_tokens, "input_tokens"),
        (result.output_tokens, "output_tokens"),
        (result.latency_ms, "latency_ms"),
    ):
        if value is not None and value < 0:
            raise InvalidProviderOutputError(details={"reason": f"negative_{field}"})


def _validate_replayed[OutputT: BaseModel](
    output_type: type[OutputT],
    record: GenerationRecordData,
    *,
    defer_application_domain_validation: bool = False,
) -> OutputT:
    expected_fingerprint = record.model_configuration.get("raw_output_fingerprint")
    if not isinstance(expected_fingerprint, str) or expected_fingerprint != _raw_output_fingerprint(
        record.raw_structured_output
    ):
        raise InternalError(details={"reason": "generation_replay_fingerprint_mismatch"})
    try:
        return output_type.model_validate_json(
            json.dumps(record.raw_structured_output, sort_keys=True, separators=(",", ":")),
            strict=True,
            context={"defer_application_domain_validation": defer_application_domain_validation},
        )
    except ValidationError as error:
        raise InternalError(details={"reason": "generation_replay_invalid"}, cause=error) from error


def _validation_summary(error: ValidationError) -> dict[str, object]:
    issues = [
        {
            "location": [str(part) for part in issue["loc"]],
            "type": issue["type"],
        }
        for issue in error.errors(include_url=False, include_context=False, include_input=False)
    ]
    return {"issue_count": len(issues), "issues": issues}


def _raw_output_fingerprint(payload: object) -> str:
    try:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise InvalidProviderOutputError(
            details={"reason": "output_not_json"}, cause=error
        ) from error
    return hashlib.sha256(raw).hexdigest()


def _safe_raw_output(result: StructuredGenerationResult | None) -> object | None:
    if result is None:
        return None
    try:
        loaded: object = json.loads(
            json.dumps(result.payload, sort_keys=True, separators=(",", ":"))
        )
        return loaded
    except (TypeError, ValueError):
        return None


def _safe_reason(error: ResearchFlowError) -> str:
    reason = error.public_details.get("reason")
    return reason if isinstance(reason, str) else error.code.value


def _failure_summary(error: ResearchFlowError) -> dict[str, object]:
    summary: dict[str, object] = {"reason": _safe_reason(error)}
    for key in (
        "provider_http_status",
        "provider_error_type",
        "provider_error_code",
        "provider_error_message",
        "schema_compiler_version",
        "schema_error_code",
        "schema_path",
    ):
        value = error.public_details.get(key)
        if key in error.public_details and (
            value is None or isinstance(value, str | int | float | bool)
        ):
            summary[key] = value
    return summary


def _attempt_metadata(
    result: StructuredGenerationResult | None,
    failure: ResearchFlowError | None,
    key: str,
) -> object | None:
    if result is not None:
        return result.model_configuration.get(key)
    if failure is not None:
        return failure.public_details.get(key)
    return None


def _latency_ms(
    result: StructuredGenerationResult | None,
    started_at: datetime,
    completed_at: datetime,
) -> int:
    if result is not None and result.latency_ms is not None:
        return result.latency_ms
    return max(0, int((completed_at - started_at).total_seconds() * 1000))


def _estimated_cost_usd(
    result: StructuredGenerationResult | None,
    configuration: Mapping[str, object],
) -> str | None:
    """Calculate an auditable estimate only when exact rates and usage exist."""
    if result is None or result.input_tokens is None or result.output_tokens is None:
        return None
    raw_input_rate = configuration.get("input_cost_per_million_usd")
    raw_output_rate = configuration.get("output_cost_per_million_usd")
    if not isinstance(raw_input_rate, str) or not isinstance(raw_output_rate, str):
        return None
    try:
        input_rate = Decimal(raw_input_rate)
        output_rate = Decimal(raw_output_rate)
    except InvalidOperation:
        return None
    if input_rate < 0 or output_rate < 0:
        return None
    estimate = (
        Decimal(result.input_tokens) * input_rate + Decimal(result.output_tokens) * output_rate
    ) / Decimal(1_000_000)
    return format(estimate.quantize(Decimal("0.00000001")), "f")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise DomainValidationError(details={"field": "clock"})
    return value.astimezone(UTC)
