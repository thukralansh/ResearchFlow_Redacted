"""Versioned wire projection: scientific choices in, canonical bookkeeping derived.

The canonical response models and their validators remain unchanged. Only this
provider boundary excludes redundant fields; supplied redundant fields are errors,
not silently overwritten. Raw wire output remains in the GenerationRecord.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Final

from pydantic import model_validator

from researchflow.api.schemas.method_extraction import MethodExtractionBatch, MethodMergeResult
from researchflow.domain.method_catalog import MethodComponentType, MethodElementType
from researchflow.domain.method_validation import classify_method_membership

METHOD_PROVIDER_DERIVATION_VERSION: Final = "rf-method-provider-derivation-v1"
_MEMBERSHIP = ("review_section", "member_role", "required")


def _derived_fields(element_type: str) -> tuple[str, ...]:
    extra: tuple[str, ...] = ()
    if element_type == "algorithm_step":
        extra = ("section_sequence_index",)
    elif element_type == "data_flow":
        extra = ("source_element_type", "target_element_type")
    return (*_MEMBERSHIP, *extra)


def _wire_schema(schema: dict[str, Any]) -> dict[str, Any]:
    schema = deepcopy(schema)
    for definition in schema.get("$defs", {}).values():
        properties = definition.get("properties", {})
        if "member_role" in properties:
            kind = properties["element_type"]["const"]
            removed = _derived_fields(kind)
            for name in removed:
                properties.pop(name, None)
            definition["required"] = [n for n in definition["required"] if n not in removed]
        elif "applicable" in properties and "not_applicable_rationale" in properties:
            properties.pop("applicable")
            definition["required"].remove("applicable")
        if definition.get("title") == "MethodEpistemicStatus":
            definition["enum"] = ["explicit", "inferred", "implementation_convention"]
    return schema


def _derive(value: Any) -> Any:
    if not isinstance(value, dict) or not isinstance(value.get("elements"), list):
        return value  # canonical structural validation supplies the diagnostic
    data = deepcopy(value)
    elements = data["elements"]
    by_id = {
        e.get("client_id"): e
        for e in elements
        if isinstance(e, dict) and isinstance(e.get("client_id"), str)
    }
    for element in elements:
        if not isinstance(element, dict):
            continue
        kind = element.get("element_type")
        if not isinstance(kind, str):
            raise ValueError("method_element_type_invalid")
        if any(name in element for name in _derived_fields(kind)):
            raise ValueError("provider_authored_derived_method_field")
        component_type = element.get("component_type")
        if kind == "component" and not isinstance(component_type, str):
            raise ValueError("component_type_missing")
        classification = classify_method_membership(
            element_type=MethodElementType(kind),
            component_type=(
                MethodComponentType(component_type)
                if kind == "component" and isinstance(component_type, str)
                else None
            ),
        )
        element.update(
            review_section=classification.review_section.value,
            member_role=classification.member_role.value,
            required=classification.required,
        )
        if kind == "algorithm_step":
            element["section_sequence_index"] = element.get("sequence_index")
        elif kind == "data_flow":
            for side in ("source", "target"):
                reference = element.get(f"{side}_client_id")
                endpoint = by_id.get(reference) if isinstance(reference, str) else None
                if endpoint is None:
                    raise ValueError("data_flow_endpoint_unresolved")
                element[f"{side}_element_type"] = endpoint.get("element_type")
    sections = data.get("sections")
    if isinstance(sections, list):
        for section in sections:
            if not isinstance(section, dict):
                continue
            if "applicable" in section:
                raise ValueError("provider_authored_derived_method_field")
            section["applicable"] = any(
                isinstance(e, dict) and e.get("review_section") == section.get("review_section")
                for e in elements
            )
    return data


class MethodProviderExtractionBatch(MethodExtractionBatch):
    """Wire v3 normalized into the unchanged, fully validated canonical batch."""

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return _wire_schema(super().model_json_schema(*args, **kwargs))

    @model_validator(mode="wrap")
    @classmethod
    def derive_bookkeeping(cls, value: Any, handler: Any) -> Any:
        # Re-enter canonical JSON validation so strict Decimal/UUID semantics
        # remain exactly those of the original provider JSON boundary.
        canonical = MethodExtractionBatch.model_validate_json(
            json.dumps(_derive(value)), strict=True
        )
        return cls.model_construct(**canonical.__dict__)


class MethodProviderMergeResult(MethodMergeResult):
    """Same wire derivation and canonical checks, plus merge accountability."""

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return _wire_schema(super().model_json_schema(*args, **kwargs))

    @model_validator(mode="wrap")
    @classmethod
    def derive_bookkeeping(cls, value: Any, handler: Any) -> Any:
        canonical = MethodMergeResult.model_validate_json(json.dumps(_derive(value)), strict=True)
        return cls.model_construct(**canonical.__dict__)
