"""OpenAI Responses API adapter for strict provider-neutral generation."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Mapping
from typing import Final, Protocol, cast

import openai
from openai import AsyncOpenAI

from researchflow.errors import InvalidProviderOutputError, ProviderFailureError
from researchflow.infrastructure.llm.openai_schema import (
    OPENAI_STRICT_SCHEMA_COMPILER_VERSION,
    CompiledOpenAISchema,
    compile_openai_strict_schema,
)
from researchflow.ports.providers import (
    JsonValue,
    StructuredGenerationRequest,
    StructuredGenerationResult,
)


class _ResponsesResource(Protocol):
    async def create(self, **kwargs: object) -> object: ...


class _OpenAIClient(Protocol):
    responses: _ResponsesResource

    async def close(self) -> None: ...


_PROVIDER_MESSAGE_MAX_CHARS: Final = 1_000
_PROVIDER_IDENTIFIER_MAX_CHARS: Final = 128
_SECRET_TOKEN = re.compile(r"(?i)\b(?:sk|rk|sess)-[a-z0-9_-]+\b")
_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[^\s,;]+")


class OpenAIResponsesStructuredGenerationProvider:
    """Call one configured model once with strict JSON-schema output."""

    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float = 180.0,
        client: _OpenAIClient | None = None,
    ) -> None:
        if not api_key.strip():
            raise ProviderFailureError(details={"reason": "missing_api_key"})
        if timeout_seconds <= 0:
            raise ProviderFailureError(details={"reason": "invalid_timeout"})
        self._timeout_seconds = timeout_seconds
        self._client: _OpenAIClient = client or cast(
            _OpenAIClient,
            AsyncOpenAI(
                api_key=api_key,
                timeout=timeout_seconds,
                max_retries=0,
            ),
        )

    async def generate(
        self,
        request: StructuredGenerationRequest,
    ) -> StructuredGenerationResult:
        if request.model_policy.provider != "openai":
            raise ProviderFailureError(details={"reason": "provider_policy_mismatch"})
        expected_timeout = request.model_policy.configuration.get("request_timeout_seconds")
        if expected_timeout != self._timeout_seconds:
            raise ProviderFailureError(details={"reason": "timeout_policy_mismatch"})
        if request.model_policy.configuration.get("automatic_retries") != 0:
            raise ProviderFailureError(details={"reason": "retry_policy_mismatch"})
        if request.model_policy.configuration.get("store") is not False:
            raise ProviderFailureError(details={"reason": "retention_policy_mismatch"})
        if (
            request.model_policy.configuration.get("schema_compiler_version")
            != OPENAI_STRICT_SCHEMA_COMPILER_VERSION
        ):
            raise ProviderFailureError(details={"reason": "schema_compiler_policy_mismatch"})

        compiled_schema = compile_openai_strict_schema(request.json_schema)

        started = time.monotonic()
        request_arguments: dict[str, object] = {
            "model": request.model_policy.model_version,
            "instructions": request.prompt,
            "input": [
                {"role": message.role, "content": message.content} for message in request.messages
            ],
            "reasoning": {"effort": request.model_policy.reasoning_version},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": request.schema_name,
                    "schema": compiled_schema.schema,
                    "strict": True,
                }
            },
            "store": False,
        }
        max_output_tokens = request.model_policy.configuration.get("max_output_tokens")
        if max_output_tokens is not None:
            if not isinstance(max_output_tokens, int) or isinstance(max_output_tokens, bool):
                raise ProviderFailureError(details={"reason": "output_bound_policy_invalid"})
            if max_output_tokens <= 0:
                raise ProviderFailureError(details={"reason": "output_bound_policy_invalid"})
            request_arguments["max_output_tokens"] = max_output_tokens
        # Reasoning models reject temperature at non-zero reasoning effort. Keep
        # the configured value in the generation identity, but only transmit it
        # for the provider-supported no-reasoning mode.
        if request.model_policy.reasoning_version == "none":
            request_arguments["temperature"] = request.model_policy.temperature
        try:
            response = await self._client.responses.create(**request_arguments)
        except Exception as error:
            raise _translate_openai_error(error, compiled_schema) from error
        latency_ms = max(0, int((time.monotonic() - started) * 1000))

        status = getattr(response, "status", None)
        if status != "completed":
            reason = "response_refused" if _has_refusal(response) else "response_incomplete"
            raise ProviderFailureError(
                details={"reason": reason, **_schema_details(compiled_schema)}
            )
        returned_model = getattr(response, "model", None)
        if not isinstance(returned_model, str) or not returned_model.strip():
            raise InvalidProviderOutputError(
                details={"reason": "model_identity_missing", **_schema_details(compiled_schema)}
            )
        snapshot_pinned = request.model_policy.configuration.get("snapshot_pinned") is True
        if snapshot_pinned and returned_model != request.model_policy.model_version:
            raise InvalidProviderOutputError(
                details={"reason": "model_identity_mismatch", **_schema_details(compiled_schema)}
            )
        output_text = getattr(response, "output_text", None)
        if not isinstance(output_text, str) or not output_text.strip():
            raise InvalidProviderOutputError(
                details={"reason": "structured_output_missing", **_schema_details(compiled_schema)}
            )
        try:
            decoded: object = json.loads(output_text)
        except json.JSONDecodeError as error:
            raise InvalidProviderOutputError(
                details={
                    "reason": "structured_output_malformed_json",
                    **_schema_details(compiled_schema),
                },
                cause=error,
            ) from error
        if not isinstance(decoded, dict):
            raise InvalidProviderOutputError(
                details={
                    "reason": "structured_output_not_object",
                    **_schema_details(compiled_schema),
                }
            )
        payload = cast(Mapping[str, JsonValue], decoded)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        usage = getattr(response, "usage", None)
        return StructuredGenerationResult(
            task_name=request.task_name,
            input_fingerprint=request.input_fingerprint,
            schema_name=request.schema_name,
            provider="openai",
            model_version=request.model_policy.model_version,
            reasoning_version=request.model_policy.reasoning_version,
            model_configuration={
                "policy_version": request.model_policy.policy_version,
                "temperature": request.model_policy.temperature,
                "provider_configuration": dict(request.model_policy.configuration),
                "provider_reported_model": returned_model,
                **_schema_details(compiled_schema),
            },
            payload=payload,
            output_fingerprint=hashlib.sha256(canonical).hexdigest(),
            input_tokens=_usage_value(usage, "input_tokens"),
            output_tokens=_usage_value(usage, "output_tokens"),
            latency_ms=latency_ms,
        )

    async def close(self) -> None:
        await self._client.close()


def _usage_value(usage: object, name: str) -> int | None:
    value = getattr(usage, name, None)
    return value if isinstance(value, int) and value >= 0 else None


def _has_refusal(response: object) -> bool:
    for output in getattr(response, "output", ()) or ():
        for content in getattr(output, "content", ()) or ():
            if getattr(content, "type", None) == "refusal":
                return True
    return False


def _retry_after_seconds(error: openai.RateLimitError) -> float | None:
    value = error.response.headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _translate_openai_error(
    error: Exception, compiled_schema: CompiledOpenAISchema
) -> ProviderFailureError:
    details: dict[str, str | int | float | None] = {}
    details.update(_schema_details(compiled_schema))
    details.update(_sanitized_provider_error(error))
    if isinstance(error, openai.APITimeoutError):
        return ProviderFailureError(details={"reason": "timeout", **details}, cause=error)
    if isinstance(error, openai.RateLimitError):
        return ProviderFailureError(
            details={
                "reason": "rate_limited",
                "retry_after_seconds": _retry_after_seconds(error),
                **details,
            },
            cause=error,
        )
    if isinstance(error, openai.AuthenticationError):
        return ProviderFailureError(
            details={"reason": "authentication_failed", **details}, cause=error
        )
    if isinstance(error, openai.PermissionDeniedError):
        return ProviderFailureError(details={"reason": "permission_denied", **details}, cause=error)
    if isinstance(error, openai.NotFoundError):
        return ProviderFailureError(details={"reason": "model_unavailable", **details}, cause=error)
    if isinstance(error, openai.BadRequestError):
        return ProviderFailureError(details={"reason": "request_rejected", **details}, cause=error)
    if isinstance(error, openai.APIConnectionError):
        return ProviderFailureError(details={"reason": "network_failure", **details}, cause=error)
    if isinstance(error, openai.APIStatusError):
        return ProviderFailureError(
            details={"reason": "provider_http_failure", **details}, cause=error
        )
    return ProviderFailureError(details={"reason": "adapter_exception", **details}, cause=error)


def _schema_details(compiled: CompiledOpenAISchema) -> dict[str, str]:
    return {
        "provider_schema_compiler_version": compiled.compiler_version,
        "provider_schema_hash": compiled.schema_hash,
    }


def _sanitized_provider_error(error: Exception) -> dict[str, str | int | None]:
    details: dict[str, str | int | None] = {}
    status = getattr(error, "status_code", None)
    if isinstance(status, int) and 100 <= status <= 599:
        details["provider_http_status"] = status

    body = getattr(error, "body", None)
    provider_error: object = body
    if isinstance(body, Mapping) and isinstance(body.get("error"), Mapping):
        provider_error = body["error"]
    if isinstance(provider_error, Mapping):
        for source_name, target_name in (
            ("type", "provider_error_type"),
            ("code", "provider_error_code"),
        ):
            value = _bounded_identifier(provider_error.get(source_name))
            if value is not None:
                details[target_name] = value
        message = _sanitized_message(provider_error.get("message"))
    else:
        message = None
    if message is None:
        message = _sanitized_message(getattr(error, "message", None))
    if message is not None:
        details["provider_error_message"] = message
    return details


def _bounded_identifier(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())[:_PROVIDER_IDENTIFIER_MAX_CHARS]
    return normalized or None


def _sanitized_message(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    normalized = _SECRET_TOKEN.sub("[REDACTED]", normalized)
    normalized = _BEARER_TOKEN.sub("Bearer [REDACTED]", normalized)
    normalized = normalized[:_PROVIDER_MESSAGE_MAX_CHARS]
    return normalized or None
