"""VerificationCheck semantic vocabulary and operational result policy."""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from researchflow.domain.blueprint_catalog import VerificationResultStatus
from researchflow.errors import DomainValidationError

VERIFICATION_CONTRACT_VERSION: Final = "rf-verification-contract-v1"


class VerificationTestType(StrEnum):
    SCIENTIFIC_OUTCOME = "scientific_outcome"
    MATHEMATICAL_CONSISTENCY = "mathematical_consistency"
    DATA_INTEGRITY = "data_integrity"
    ALGORITHMIC_BEHAVIOR = "algorithmic_behavior"
    VALIDITY_CONDITION = "validity_condition"
    COMPUTATIONAL_PERFORMANCE = "computational_performance"
    INTEGRATION = "integration"


def validate_result_status(value: str) -> str:
    try:
        return VerificationResultStatus(value).value
    except ValueError as error:
        raise DomainValidationError(
            details={"reason": "verification_result_status_invalid"}
        ) from error
