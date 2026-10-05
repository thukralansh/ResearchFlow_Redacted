"""Versioned prompt loading with exact prompt/schema identity."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pydantic import BaseModel

from researchflow.errors import ResourceConflictError, ResourceNotFoundError
from researchflow.ports.providers import JsonValue

PROMPT_MAX_BYTES: Final = 256 * 1024
_IDENTIFIER: Final = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")


def _canonical_hash(value: object) -> str:
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _require_identifier(value: str, field: str) -> None:
    if _IDENTIFIER.fullmatch(value) is None:
        raise ResourceNotFoundError(details={"resource": field})


@dataclass(frozen=True, slots=True)
class PromptBinding:
    """One immutable prompt version coupled to one exact output schema."""

    task_name: str
    prompt_version: str
    prompt_hash: str
    content: str
    schema_name: str
    schema_version: str
    schema_hash: str
    json_schema: dict[str, JsonValue]

    @property
    def identity(self) -> str:
        return f"{self.task_name}:{self.prompt_version}:{self.prompt_hash}"


class PromptRegistry:
    """Load prompt bytes once and reject version or schema drift."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or _default_prompt_root()
        self._bindings: dict[tuple[str, str], PromptBinding] = {}

    def load[OutputT: BaseModel](
        self,
        *,
        task_name: str,
        prompt_version: str,
        schema_name: str,
        schema_version: str,
        output_type: type[OutputT],
    ) -> PromptBinding:
        """Resolve and bind exact prompt bytes to a canonical Pydantic schema."""
        for value, field in (
            (task_name, "task_name"),
            (prompt_version, "prompt_version"),
            (schema_name, "schema_name"),
            (schema_version, "schema_version"),
        ):
            _require_identifier(value, field)

        path = self._root / prompt_version / f"{task_name}.md"
        try:
            raw = path.read_bytes()
        except (FileNotFoundError, IsADirectoryError) as error:
            raise ResourceNotFoundError(details={"resource": "prompt"}) from error
        if not raw or len(raw) > PROMPT_MAX_BYTES:
            raise ResourceConflictError(details={"reason": "prompt_size_invalid"})
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ResourceConflictError(details={"reason": "prompt_not_utf8"}) from error
        if not content.strip():
            raise ResourceConflictError(details={"reason": "prompt_blank"})

        prompt_hash = hashlib.sha256(raw).hexdigest()
        schema = output_type.model_json_schema()
        schema_hash = _canonical_hash(schema)
        binding = PromptBinding(
            task_name=task_name,
            prompt_version=prompt_version,
            prompt_hash=prompt_hash,
            content=content,
            schema_name=schema_name,
            schema_version=schema_version,
            schema_hash=schema_hash,
            json_schema=schema,
        )
        key = (task_name, prompt_version)
        existing = self._bindings.get(key)
        if existing is None:
            self._bindings[key] = binding
            return binding
        if existing.prompt_hash != prompt_hash:
            raise ResourceConflictError(details={"reason": "prompt_version_content_changed"})
        if (
            existing.schema_name,
            existing.schema_version,
            existing.schema_hash,
        ) != (schema_name, schema_version, schema_hash):
            raise ResourceConflictError(details={"reason": "prompt_schema_binding_changed"})
        return existing


def _default_prompt_root() -> Path:
    packaged = Path(__file__).resolve().parent / "files"
    if packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parents[3] / "prompts"
