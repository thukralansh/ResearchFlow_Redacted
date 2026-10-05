"""Versioned compilation into the OpenAI strict Structured Outputs subset."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Final, cast

from researchflow.errors import ProviderFailureError
from researchflow.ports.providers import JsonValue

OPENAI_STRICT_SCHEMA_COMPILER_VERSION: Final = "rf-openai-strict-schema-v2"
# Recognize Pydantic's Decimal string alternatives, never arbitrary lookarounds.
# Numeric output retains the sibling number constraints; Decimal precision and
# domain validators remain authoritative in StructuredGenerationService.
_DECIMAL_PREFIX: Final = r"^(?!^[-+.]*$)[+-]?0*"
_DECIMAL_PATTERNS: Final = tuple(
    re.compile(re.escape(_DECIMAL_PREFIX + suffix).replace("DIGITS", r"[0-9]+"))
    for suffix in (
        r"\d*\.?\d*$",
        r"\d*\.?\d{0,DIGITS}0*$",
        r"(?:\d{0,DIGITS}|(?=[\d.]{1,DIGITS}0*$)\d*\.\d*0*$)",
        r"(?:\d{0,DIGITS}|(?=[\d.]{1,DIGITS}0*$)\d{0,DIGITS}\.\d{0,DIGITS}0*$)",
    )
)
_UNSUPPORTED_KEYWORDS: Final = frozenset(
    {
        "allOf",
        "contains",
        "dependentRequired",
        "dependentSchemas",
        "else",
        "if",
        "maxContains",
        "maxProperties",
        "minContains",
        "minProperties",
        "not",
        "patternProperties",
        "propertyNames",
        "then",
        "unevaluatedItems",
        "unevaluatedProperties",
        "uniqueItems",
    }
)


@dataclass(frozen=True, slots=True)
class CompiledOpenAISchema:
    """One deterministic provider schema and its immutable identity."""

    schema: dict[str, JsonValue]
    compiler_version: str
    schema_hash: str


def compile_openai_strict_schema(schema: object) -> CompiledOpenAISchema:
    """Project supported wire representations; original domain validation still runs."""
    try:
        loaded = json.loads(json.dumps(schema, separators=(",", ":")))
    except (TypeError, ValueError) as error:
        raise _schema_error("schema_not_json") from error
    if not isinstance(loaded, dict):
        raise _schema_error("root_not_object")

    root = cast(dict[str, object], loaded)
    _compile_node(root, path=("$",))
    if root.get("type") != "object" or "anyOf" in root:
        raise _schema_error("root_not_object", path=("$",))
    _validate_references(root)
    canonical = json.dumps(root, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return CompiledOpenAISchema(
        schema=cast(dict[str, JsonValue], root),
        compiler_version=OPENAI_STRICT_SCHEMA_COMPILER_VERSION,
        schema_hash=hashlib.sha256(canonical).hexdigest(),
    )


def _compile_node(node: dict[str, object], *, path: tuple[str, ...]) -> None:
    unsupported = sorted(_UNSUPPORTED_KEYWORDS.intersection(node))
    if unsupported:
        raise _schema_error(
            "unsupported_keyword",
            path=(*path, unsupported[0]),
        )
    if "oneOf" in node:
        if "anyOf" in node:
            raise _schema_error("conflicting_union_keywords", path=path)
        node["anyOf"] = node.pop("oneOf")
    node.pop("discriminator", None)
    node.pop("default", None)
    _project_decimal_number(node)
    if "pattern" in node:
        _validate_pattern(node["pattern"], path=(*path, "pattern"))

    definitions = node.get("$defs")
    if definitions is not None:
        if not isinstance(definitions, dict):
            raise _schema_error("definitions_not_object", path=(*path, "$defs"))
        for name, definition in definitions.items():
            if not isinstance(name, str) or not isinstance(definition, dict):
                raise _schema_error("definition_invalid", path=(*path, "$defs", str(name)))
            _compile_node(definition, path=(*path, "$defs", name))

    union = node.get("anyOf")
    if union is not None:
        if not isinstance(union, list) or not union:
            raise _schema_error("any_of_invalid", path=(*path, "anyOf"))
        for index, branch in enumerate(union):
            if not isinstance(branch, dict):
                raise _schema_error("any_of_branch_invalid", path=(*path, "anyOf", str(index)))
            _compile_node(branch, path=(*path, "anyOf", str(index)))

    items = node.get("items")
    if node.get("type") == "array" and items is None:
        raise _schema_error("array_items_missing", path=(*path, "items"))
    if items is not None:
        if not isinstance(items, dict):
            raise _schema_error("array_items_invalid", path=(*path, "items"))
        _compile_node(items, path=(*path, "items"))

    properties = node.get("properties")
    is_object = node.get("type") == "object" or properties is not None
    if not is_object:
        return
    if not isinstance(properties, dict):
        raise _schema_error("object_properties_missing", path=(*path, "properties"))
    additional = node.get("additionalProperties")
    if additional not in (None, False):
        raise _schema_error("additional_properties_not_false", path=path)

    required = node.get("required", [])
    if not isinstance(required, list) or any(not isinstance(value, str) for value in required):
        raise _schema_error("required_invalid", path=(*path, "required"))
    unknown_required = sorted(set(required).difference(properties))
    if unknown_required:
        raise _schema_error("required_property_unknown", path=(*path, unknown_required[0]))

    for name, property_schema in properties.items():
        if not isinstance(name, str) or not isinstance(property_schema, dict):
            raise _schema_error("property_schema_invalid", path=(*path, "properties", str(name)))
        _compile_node(property_schema, path=(*path, "properties", name))

    node["additionalProperties"] = False
    node["required"] = list(properties)


def _project_decimal_number(node: dict[str, object]) -> None:
    union = node.get("anyOf")
    if not isinstance(union, list) or len(union) not in (2, 3):
        return
    if len(union) == 3 and union[2] != {"type": "null"}:
        return
    number, string = union[:2]
    if not isinstance(number, dict) or not isinstance(string, dict):
        return
    if number.get("type") != "number" or set(string) != {"type", "pattern"}:
        return
    pattern = string.get("pattern")
    if string["type"] != "string" or not isinstance(pattern, str):
        return
    # Pydantic releases have emitted both single and doubled regex backslashes.
    if not any(rule.fullmatch(pattern.replace("\\\\", "\\")) for rule in _DECIMAL_PATTERNS):
        return
    # Keep any surrounding constraints untouched, including nullable outer unions.
    node["anyOf"] = [number, *union[2:]]


def _validate_pattern(pattern: object, *, path: tuple[str, ...]) -> None:
    """Fail closed on regex syntax outside the bounded portable provider subset."""
    if not isinstance(pattern, str):
        raise _schema_error("pattern_invalid", path=path)
    try:
        re.compile(pattern)
    except re.error as error:
        raise _schema_error("pattern_invalid", path=path) from error
    index = 0
    in_class = False
    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            escaped = pattern[index + 1]
            # No backreferences, engine-specific anchors, or unknown escapes.
            if escaped.isalnum() and escaped not in "dDsSwWnrtfv":
                raise _schema_error("regex_construct_unsupported", path=path)
            index += 2
            continue
        if char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        elif not in_class:
            if pattern.startswith("(?", index) and not pattern.startswith("(?:", index):
                raise _schema_error("regex_construct_unsupported", path=path)
            if char in "*+?}" and pattern[index + 1 : index + 2] == "+":
                raise _schema_error("regex_construct_unsupported", path=path)
        index += 1


def _validate_references(root: dict[str, object]) -> None:
    definitions = root.get("$defs", {})
    if not isinstance(definitions, dict):
        raise _schema_error("definitions_not_object", path=("$", "$defs"))

    def visit(node: dict[str, object], path: tuple[str, ...]) -> None:
        reference = node.get("$ref")
        if reference is not None:
            if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
                raise _schema_error("reference_unsupported", path=(*path, "$ref"))
            name = reference.removeprefix("#/$defs/")
            if not name or "/" in name or name not in definitions:
                raise _schema_error("reference_unresolved", path=(*path, "$ref"))
        for key in ("anyOf",):
            children = node.get(key)
            if isinstance(children, list):
                for index, child in enumerate(children):
                    if isinstance(child, dict):
                        visit(child, (*path, key, str(index)))
        items = node.get("items")
        if isinstance(items, dict):
            visit(items, (*path, "items"))
        properties = node.get("properties")
        if isinstance(properties, dict):
            for name, child in properties.items():
                if isinstance(child, dict):
                    visit(child, (*path, "properties", str(name)))
        nested_definitions = node.get("$defs")
        if isinstance(nested_definitions, dict):
            for name, child in nested_definitions.items():
                if isinstance(child, dict):
                    visit(child, (*path, "$defs", str(name)))

    visit(root, ("$",))


def _schema_error(reason: str, *, path: tuple[str, ...] = ("$",)) -> ProviderFailureError:
    return ProviderFailureError(
        details={
            "reason": "structured_output_schema_unsupported",
            "schema_compiler_version": OPENAI_STRICT_SCHEMA_COMPILER_VERSION,
            "schema_error_code": reason,
            "schema_path": "/".join(path)[:1_000],
        }
    )
