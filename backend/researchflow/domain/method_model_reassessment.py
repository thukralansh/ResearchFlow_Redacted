"""Deterministic  reassessment and section-authority identities."""

import hashlib
import json
from typing import Final

METHOD_REASSESSMENT_POLICY_VERSION: Final = "rf-method-reassessment-v1"
METHOD_REASSESSMENT_SECTION_POLICY_VERSION: Final = "rf-method-section-authority-v1"
METHOD_REASSESSMENT_TASK_NAME: Final = "reassess_method_model"
METHOD_REASSESSMENT_PROMPT_VERSION: Final = "v1"
METHOD_REASSESSMENT_SCHEMA_NAME: Final = "method_model_reassessment"
METHOD_REASSESSMENT_SCHEMA_VERSION: Final = "v1"


def reassessment_fingerprint(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
