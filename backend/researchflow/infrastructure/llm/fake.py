"""Deterministic structured-generation fixture provider."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from researchflow.errors import InvalidProviderOutputError, UnknownProviderTaskError
from researchflow.ports.providers import (
    JsonValue,
    StructuredGenerationRequest,
    StructuredGenerationResult,
)

type FixtureKey = tuple[str, str]
type FixtureValue = Mapping[str, JsonValue] | bytes


class FakeStructuredGenerationProvider:
    """Return immutable fixture bytes keyed by task and input fingerprint."""

    def __init__(
        self,
        fixtures: Mapping[FixtureKey, FixtureValue],
        *,
        model: str = "fake-v1",
        reasoning_version: str | None = "fake-reasoning-v1",
    ) -> None:
        self._model = model
        self._reasoning_version = reasoning_version
        self._fixtures: dict[FixtureKey, bytes] = {}
        for key, payload in fixtures.items():
            if isinstance(payload, bytes):
                self._fixtures[key] = bytes(payload)
                continue
            try:
                serialized = json.dumps(
                    payload,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            except (TypeError, ValueError) as error:
                raise InvalidProviderOutputError(
                    details={"reason": "fixture_not_json"},
                    cause=error,
                ) from error
            self._fixtures[key] = serialized

    @classmethod
    def from_json_files(
        cls,
        fixtures: Mapping[FixtureKey, Path],
        *,
        model: str = "fake-v1",
        reasoning_version: str | None = "fake-reasoning-v1",
    ) -> FakeStructuredGenerationProvider:
        """Build a fixture registry from immutable JSON file bytes."""
        loaded: dict[FixtureKey, Mapping[str, JsonValue]] = {}
        for key, path in fixtures.items():
            try:
                payload: object = json.loads(path.read_bytes())
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise InvalidProviderOutputError(
                    details={"reason": "fixture_malformed_json"}, cause=error
                ) from error
            if not isinstance(payload, dict):
                raise InvalidProviderOutputError(details={"reason": "fixture_not_object"})
            loaded[key] = cast(Mapping[str, JsonValue], payload)
        return cls(loaded, model=model, reasoning_version=reasoning_version)

    async def generate(
        self,
        request: StructuredGenerationRequest,
    ) -> StructuredGenerationResult:
        key = (request.task_name, request.input_fingerprint)
        try:
            serialized = self._fixtures[key]
        except KeyError as error:
            raise UnknownProviderTaskError(
                details={
                    "task_name": request.task_name,
                    "input_fingerprint": request.input_fingerprint,
                }
            ) from error

        try:
            payload = json.loads(serialized)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise InvalidProviderOutputError(
                details={"reason": "fixture_malformed_json"}, cause=error
            ) from error
        if not isinstance(payload, dict):
            raise InvalidProviderOutputError(details={"reason": "fixture_not_object"})
        output_fingerprint = hashlib.sha256(serialized).hexdigest()
        return StructuredGenerationResult(
            task_name=request.task_name,
            input_fingerprint=request.input_fingerprint,
            schema_name=request.schema_name,
            provider="fake",
            model_version=self._model,
            reasoning_version=self._reasoning_version,
            model_configuration={
                "policy_version": request.model_policy.policy_version,
                "temperature": request.model_policy.temperature,
                "provider_configuration": dict(request.model_policy.configuration),
            },
            payload=payload,
            output_fingerprint=output_fingerprint,
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
        )
