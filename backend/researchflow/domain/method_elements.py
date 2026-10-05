"""Typed semantic payload validation for independently versioned MethodElements."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from uuid import UUID

from researchflow.domain.method_catalog import (
    DATA_SHAPE_MAX_DIMENSIONS,
    MethodComponentType,
    MethodElementType,
    MethodEpistemicStatus,
    MethodEquationType,
    MethodSymbolType,
    ParameterRole,
    ValidityConditionType,
    VariableRole,
)
from researchflow.errors import DomainValidationError


def _required(value: str, *, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise DomainValidationError(details={"field": field, "reason": "blank"})
    return normalized


def _optional(value: str | None, *, field: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise DomainValidationError(details={"field": field, "reason": "blank"})
    return normalized


@dataclass(frozen=True, slots=True)
class MethodComponentPayload:
    component_type: MethodComponentType
    name: str
    description: str

    def validated(self) -> MethodComponentPayload:
        return replace(
            self,
            name=_required(self.name, field="name"),
            description=_required(self.description, field="description"),
        )


@dataclass(frozen=True, slots=True)
class MethodEquationPayload:
    label: str | None
    latex_expression: str
    normalized_expression: str | None
    explanation: str
    equation_type: MethodEquationType

    def validated(self) -> MethodEquationPayload:
        return replace(
            self,
            label=_optional(self.label, field="label"),
            latex_expression=_required(self.latex_expression, field="latex_expression"),
            normalized_expression=_optional(
                self.normalized_expression,
                field="normalized_expression",
            ),
            explanation=_required(self.explanation, field="explanation"),
        )


@dataclass(frozen=True, slots=True)
class ScalarValue:
    value: int | float | str | bool

    def __post_init__(self) -> None:
        if isinstance(self.value, str) and not self.value.strip():
            raise DomainValidationError(details={"field": "value", "reason": "blank"})


@dataclass(frozen=True, slots=True)
class RangeValue:
    minimum: Decimal
    maximum: Decimal
    minimum_inclusive: bool
    maximum_inclusive: bool

    def __post_init__(self) -> None:
        if self.minimum > self.maximum:
            raise DomainValidationError(details={"field": "range", "reason": "inverted"})


type ParameterValue = ScalarValue | RangeValue


@dataclass(frozen=True, slots=True)
class MethodSymbolPayload:
    symbol_type: MethodSymbolType
    symbol: str
    canonical_name: str
    definition: str
    units: str | None
    domain_or_data_type: str | None
    constraints: str | None
    role: str
    variable_role: VariableRole | None
    parameter_role: ParameterRole | None
    value_or_range: ParameterValue | None

    def validated(self) -> MethodSymbolPayload:
        if self.symbol_type is MethodSymbolType.VARIABLE:
            if (
                self.variable_role is None
                or self.parameter_role is not None
                or self.value_or_range is not None
            ):
                raise DomainValidationError(details={"reason": "variable_role_family_invalid"})
        elif self.parameter_role is None or self.variable_role is not None:
            raise DomainValidationError(details={"reason": "parameter_role_family_invalid"})
        return replace(
            self,
            symbol=_required(self.symbol, field="symbol"),
            canonical_name=_required(self.canonical_name, field="canonical_name"),
            definition=_required(self.definition, field="definition"),
            units=_optional(self.units, field="units"),
            domain_or_data_type=_optional(
                self.domain_or_data_type,
                field="domain_or_data_type",
            ),
            constraints=_optional(self.constraints, field="constraints"),
            role=_required(self.role, field="role"),
        )


@dataclass(frozen=True, slots=True)
class MethodAlgorithmStepPayload:
    sequence_index: int
    title: str
    action: str
    conditions: str | None

    def validated(self) -> MethodAlgorithmStepPayload:
        if self.sequence_index < 0:
            raise DomainValidationError(details={"field": "sequence_index", "reason": "negative"})
        return replace(
            self,
            title=_required(self.title, field="title"),
            action=_required(self.action, field="action"),
            conditions=_optional(self.conditions, field="conditions"),
        )


@dataclass(frozen=True, slots=True)
class MethodEndpoint:
    element_id: UUID
    element_type: MethodElementType

    def __post_init__(self) -> None:
        if self.element_type not in {
            MethodElementType.COMPONENT,
            MethodElementType.SYMBOL,
            MethodElementType.ALGORITHM_STEP,
        }:
            raise DomainValidationError(details={"reason": "data_flow_endpoint_type_invalid"})


@dataclass(frozen=True, slots=True)
class DataTypeOrShape:
    data_type: str | None = None
    shape: tuple[int | str | None, ...] | None = None

    def validated(self) -> DataTypeOrShape:
        data_type = _optional(self.data_type, field="data_type")
        if data_type is None and self.shape is None:
            raise DomainValidationError(details={"reason": "data_type_or_shape_empty"})
        if self.shape is not None:
            if len(self.shape) > DATA_SHAPE_MAX_DIMENSIONS:
                raise DomainValidationError(details={"reason": "data_shape_too_large"})
            for dimension in self.shape:
                if isinstance(dimension, bool):
                    raise DomainValidationError(details={"reason": "data_shape_dimension_invalid"})
                if isinstance(dimension, int) and dimension <= 0:
                    raise DomainValidationError(details={"reason": "data_shape_dimension_invalid"})
                if isinstance(dimension, str) and not dimension.strip():
                    raise DomainValidationError(details={"reason": "data_shape_dimension_invalid"})
        return replace(self, data_type=data_type)


@dataclass(frozen=True, slots=True)
class MethodDataFlowPayload:
    source: MethodEndpoint
    target: MethodEndpoint
    transformation: str
    data_type_or_shape: DataTypeOrShape | None

    def validated(self) -> MethodDataFlowPayload:
        if self.source.element_id == self.target.element_id:
            raise DomainValidationError(details={"reason": "data_flow_self_reference"})
        return replace(
            self,
            transformation=_required(self.transformation, field="transformation"),
            data_type_or_shape=(
                None if self.data_type_or_shape is None else self.data_type_or_shape.validated()
            ),
        )


@dataclass(frozen=True, slots=True)
class MethodValidityConditionPayload:
    statement: str
    condition_type: ValidityConditionType
    consequence_if_violated: str

    def validated(self) -> MethodValidityConditionPayload:
        return replace(
            self,
            statement=_required(self.statement, field="statement"),
            consequence_if_violated=_required(
                self.consequence_if_violated,
                field="consequence_if_violated",
            ),
        )


type MethodElementSubtypePayload = (
    MethodComponentPayload
    | MethodEquationPayload
    | MethodSymbolPayload
    | MethodAlgorithmStepPayload
    | MethodDataFlowPayload
    | MethodValidityConditionPayload
)

_SUBTYPE_BY_ELEMENT_TYPE: dict[MethodElementType, type[MethodElementSubtypePayload]] = {
    MethodElementType.COMPONENT: MethodComponentPayload,
    MethodElementType.EQUATION: MethodEquationPayload,
    MethodElementType.SYMBOL: MethodSymbolPayload,
    MethodElementType.ALGORITHM_STEP: MethodAlgorithmStepPayload,
    MethodElementType.DATA_FLOW: MethodDataFlowPayload,
    MethodElementType.VALIDITY_CONDITION: MethodValidityConditionPayload,
}


@dataclass(frozen=True, slots=True)
class MethodElementPayload:
    element_type: MethodElementType
    epistemic_status: MethodEpistemicStatus
    confidence: Decimal | None
    subtype: MethodElementSubtypePayload

    def validated(self) -> MethodElementPayload:
        expected = _SUBTYPE_BY_ELEMENT_TYPE[self.element_type]
        if not isinstance(self.subtype, expected):
            raise DomainValidationError(details={"reason": "method_element_subtype_mismatch"})
        if self.confidence is not None and not Decimal("0") <= self.confidence <= Decimal("1"):
            raise DomainValidationError(
                details={"field": "confidence", "reason": "outside_unit_interval"}
            )
        subtype = self.subtype.validated()
        return replace(self, subtype=subtype)
