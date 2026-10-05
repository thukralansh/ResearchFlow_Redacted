"""Immutable policy inputs for knowledge-guided Repository Stage B ranking."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from researchflow.errors import DomainValidationError

RANKING_VERSION: Final = "rf-repository-ranking-v1"
ANALYSIS_VERSION: Final = "rf-python-symbols-v1"
BYTE_BUDGET_POLICY_VERSION: Final = "rf-repository-byte-budget-v1"
DEFAULT_CANDIDATE_LIMIT: Final = 30
DEFAULT_INSPECTION_LIMIT: Final = 12
DEFAULT_MAX_FILE_BYTES: Final = 256 * 1024
DEFAULT_AGGREGATE_BYTES: Final = 3 * 1024 * 1024

_TOKEN = re.compile(r"[A-Za-z0-9_]+")
_STOP_WORDS: Final = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "use",
        "using",
        "with",
        "without",
        "whether",
        "which",
        "where",
        "when",
    }
)


@dataclass(frozen=True, slots=True)
class RankingWeights:
    guidance_term: Decimal = Decimal("0.40")
    expected_role: Decimal = Decimal("0.30")
    stage_a_reference: Decimal = Decimal("0.20")
    implementation_source: Decimal = Decimal("0.10")

    def validate(self) -> RankingWeights:
        values = (
            self.guidance_term,
            self.expected_role,
            self.stage_a_reference,
            self.implementation_source,
        )
        if any(value < 0 for value in values) or sum(values) != Decimal("1.00"):
            raise DomainValidationError(details={"reason": "repository_ranking_weights_invalid"})
        return self

    def identity(self) -> dict[str, str]:
        self.validate()
        return {
            "guidance_term": str(self.guidance_term),
            "expected_role": str(self.expected_role),
            "stage_a_reference": str(self.stage_a_reference),
            "implementation_source": str(self.implementation_source),
        }


@dataclass(frozen=True, slots=True)
class InspectionLimits:
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT
    inspection_limit: int = DEFAULT_INSPECTION_LIMIT
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    aggregate_bytes: int = DEFAULT_AGGREGATE_BYTES

    def validate(self) -> InspectionLimits:
        if (
            min(
                self.candidate_limit,
                self.inspection_limit,
                self.max_file_bytes,
                self.aggregate_bytes,
            )
            <= 0
            or self.inspection_limit > self.candidate_limit
        ):
            raise DomainValidationError(details={"reason": "repository_inspection_limits_invalid"})
        return self

    def identity(self) -> dict[str, int]:
        self.validate()
        return {
            "candidate_limit": self.candidate_limit,
            "inspection_limit": self.inspection_limit,
            "max_file_bytes": self.max_file_bytes,
            "aggregate_bytes": self.aggregate_bytes,
        }


@dataclass(frozen=True, slots=True)
class GuidanceInput:
    source_type: str
    source_id: str
    text: str
    expected_roles: tuple[str, ...] = ()

    def identity(self) -> dict[str, object]:
        return {
            "source_type": self.source_type,
            "source_id": self.source_id,
            "text": self.text.strip(),
            "expected_roles": sorted(set(self.expected_roles)),
        }


@dataclass(frozen=True, slots=True)
class RepositoryGuidance:
    project_id: str
    research_question_id: str
    question_text: str
    objective: str
    scope: str | None
    research_depth: str
    method_model_id: str
    method_model_fingerprint: str
    mental_model_id: str
    mental_model_fingerprint: str
    relationship_id: str
    relationship_type: str
    authority_level: str
    discovery_run_id: str
    discovery_tree_hash: str
    accepted_inputs: tuple[GuidanceInput, ...]

    def identity(self) -> dict[str, object]:
        return {
            "project_id": self.project_id,
            "research_question_id": self.research_question_id,
            "question_text": self.question_text.strip(),
            "objective": self.objective.strip(),
            "scope": None if self.scope is None else self.scope.strip(),
            "research_depth": self.research_depth,
            "method_model_id": self.method_model_id,
            "method_model_fingerprint": self.method_model_fingerprint,
            "mental_model_id": self.mental_model_id,
            "mental_model_fingerprint": self.mental_model_fingerprint,
            "relationship_id": self.relationship_id,
            "relationship_type": self.relationship_type,
            "authority_level": self.authority_level,
            "discovery_run_id": self.discovery_run_id,
            "discovery_tree_hash": self.discovery_tree_hash,
            "accepted_inputs": [
                item.identity()
                for item in sorted(
                    self.accepted_inputs,
                    key=lambda value: (value.source_type, value.source_id),
                )
            ],
        }

    def fingerprint(
        self,
        *,
        ranking_version: str,
        analysis_version: str,
        byte_budget_policy_version: str,
        weights: RankingWeights,
        limits: InspectionLimits,
    ) -> str:
        payload = {
            "guidance": self.identity(),
            "ranking_version": ranking_version,
            "analysis_version": analysis_version,
            "byte_budget_policy_version": byte_budget_policy_version,
            "weights": weights.identity(),
            "limits": limits.identity(),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def terms(self) -> tuple[str, ...]:
        values = [self.question_text, self.objective, self.scope or ""]
        values.extend(item.text for item in self.accepted_inputs)
        return tuple(sorted({token for value in values for token in tokenize(value)}))

    def expected_roles(self) -> tuple[str, ...]:
        return tuple(
            sorted({role for item in self.accepted_inputs for role in item.expected_roles})
        )


def tokenize(value: str) -> tuple[str, ...]:
    """Return deterministic case-insensitive lexical terms for ranking only."""
    tokens: list[str] = []
    for match in _TOKEN.finditer(value):
        for token in match.group(0).casefold().split("_"):
            if len(token) > 1 and token not in _STOP_WORDS:
                tokens.append(token)
    return tuple(tokens)


def expected_roles_for_element(element_type: str) -> tuple[str, ...]:
    return {
        "component": ("model_definition", "training", "evaluation"),
        "equation": ("loss", "model_definition"),
        "symbol": ("configuration", "model_definition"),
        "algorithm_step": ("preprocessing", "training", "evaluation"),
        "data_flow": ("data_loading", "preprocessing"),
        "validity_condition": ("evaluation", "configuration", "test"),
    }.get(element_type, ())
