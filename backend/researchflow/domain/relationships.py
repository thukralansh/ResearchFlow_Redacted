"""Typed, canonical relationship compatibility policy for ResearchFlow."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import UUID

from researchflow.domain.knowledge_catalog import KnowledgeReviewStatus
from researchflow.domain.model_catalog import MentalModelMemberRole
from researchflow.errors import DomainValidationError, ResourceConflictError

RELATIONSHIP_POLICY_VERSION: Final = "rf-relationship-policy-v1"


class EndpointType(StrEnum):
    RESEARCH_QUESTION = "research_question"
    SOURCE = "source"
    REPOSITORY = "repository"
    REPOSITORY_SNAPSHOT = "repository_snapshot"
    SOURCE_CHUNK = "source_chunk"
    REPOSITORY_COMPONENT = "repository_component"
    CLAIM = "claim"
    ASSUMPTION = "assumption"
    KNOWLEDGE_GAP = "knowledge_gap"
    REQUIREMENT = "implementation_requirement"
    AMBIGUITY = "ambiguity"
    METHOD_MODEL = "method_model"
    METHOD_COMPONENT = "method_component"
    EQUATION = "equation"
    METHOD_SYMBOL_VARIABLE = "method_symbol_variable"
    METHOD_SYMBOL_PARAMETER = "method_symbol_parameter"
    ALGORITHM_STEP = "algorithm_step"
    DATA_FLOW = "data_flow"
    VALIDITY_CONDITION = "validity_condition"
    HUMAN_JUDGMENT = "human_judgment"
    IMPLEMENTATION_STEP = "implementation_step"
    VERIFICATION_CHECK = "verification_check"
    EVIDENCE_LINK = "evidence_link"
    MENTAL_MODEL = "mental_model"
    BLUEPRINT = "blueprint"


class RelationshipType(StrEnum):
    ADDRESSED_BY = "addressed_by"
    EVIDENCED_BY = "evidenced_by"
    IMPLEMENTED_BY = "implemented_by"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    SUPPORTED_BY = "supported_by"
    DEPENDS_ON = "depends_on"
    CONTAINS = "contains"
    USES = "uses"
    OPERATIONALIZED_BY = "operationalized_by"
    CONSUMES = "consumes"
    PRODUCES = "produces"
    CONCERNS = "concerns"
    AFFECTS = "affects"
    DERIVED_FROM = "derived_from"
    QUALIFIES = "qualifies"
    IMPLEMENTS = "implements"
    CONFIGURES = "configures"
    MAPS_TO = "maps_to"
    ABSENT_FROM_REPO = "absent_from_repo"
    REVEALS = "reveals"
    RESOLVES = "resolves"
    SATISFIES = "satisfies"
    TESTS = "tests"
    VALIDATES = "validates"
    INCLUDES = "includes"
    INCLUDES_METHOD = "includes_method"
    ADDRESSES = "addresses"


class StorageStrategy(StrEnum):
    METHOD_MODEL_FOREIGN_KEY = "method_models.research_question_id"
    RESEARCH_QUESTION_SOURCE = "research_question_sources"
    PAPER_REPOSITORY_RELATIONSHIP = "paper_repository_relationships"
    EVIDENCE_LINK = "evidence_links"
    OBJECT_LINK = "object_links"
    METHOD_MODEL_MEMBER = "method_model_members"
    METHOD_DATA_FLOW = "method_data_flows"
    PAPER_CODE_MAPPING = "paper_code_mappings"
    JUDGMENT_FOREIGN_KEYS = "judgment_foreign_keys"
    VERIFICATION_CHECK_FOREIGN_KEY = "verification_checks.implementation_step_id"
    OBJECT_LINK_AND_DEPENDENCY = "object_links+artifact_dependencies"
    MENTAL_MODEL_MEMBER = "mental_model_members"
    MENTAL_MODEL_METHOD_FOREIGN_KEY = "mental_models.method_model_id"
    MENTAL_MODEL_QUESTION_FOREIGN_KEY = "mental_models.research_question_id"
    BLUEPRINT_MENTAL_MODEL_FOREIGN_KEY = "implementation_blueprints.mental_model_id"


class Cardinality(StrEnum):
    MANY_TO_MANY = "many_to_many"
    ONE_TO_MANY = "one_to_many"
    MANY_TO_ONE = "many_to_one"
    ONE_CURRENT_PER_SOURCE = "one_current_per_source"
    AT_MOST_ONE_CURRENT = "at_most_one_current"


class ReviewPolicy(StrEnum):
    AUTOMATIC = "automatic"
    USER_CONFIRMED = "user_confirmed"
    BUNDLED_WITH_CANDIDATE = "bundled_with_candidate"
    EXPLICIT_WHEN_MATERIAL = "explicit_when_material"
    MAPPING_LEVEL = "mapping_level"
    METHOD_MODEL_REVIEW = "method_model_review"
    GROUPED_SECTION_REVIEW = "grouped_section_review"
    ALWAYS_REVIEWABLE = "always_reviewable"
    HUMAN_REVIEWED = "human_reviewed"
    BLUEPRINT_REVIEW = "blueprint_review"
    MODEL_COMPOSITION = "model_composition"


class StalenessEffect(StrEnum):
    QUESTION_REFINEMENT = "question_refinement_stales_target"
    SOURCE_ROLE_REVISION = "source_role_revision_stales_relevance"
    REPOSITORY_ROLE_REVISION = "repository_role_revision_reassesses_evidence"
    EVIDENCE_CHANGE = "evidence_change_revalidates_grounding"
    EVIDENCE_CONTRADICTION = "evidence_contradiction_challenges_target"
    SNAPSHOT_CHANGE = "snapshot_change_stales_relation"
    SOURCE_REVISION = "source_revision_reassesses_target"
    TARGET_REVISION = "target_revision_stales_consumers"
    EITHER_REVISION = "either_revision_stales_consumers"
    MODEL_COMPOSITION = "member_revision_stales_model"
    AMBIGUITY_BLOCKING = "blocking_ambiguity_blocks_consumers"
    JUDGMENT_REVISION = "judgment_revision_stales_consumers"
    MODEL_REVISION = "model_revision_stales_target"
    NONE = "none"


METHOD_ELEMENTS: Final = frozenset(
    {
        EndpointType.METHOD_COMPONENT,
        EndpointType.EQUATION,
        EndpointType.METHOD_SYMBOL_VARIABLE,
        EndpointType.METHOD_SYMBOL_PARAMETER,
        EndpointType.ALGORITHM_STEP,
        EndpointType.DATA_FLOW,
        EndpointType.VALIDITY_CONDITION,
    }
)
EVIDENCE_TARGETS: Final = frozenset(
    {
        EndpointType.CLAIM,
        EndpointType.ASSUMPTION,
        EndpointType.KNOWLEDGE_GAP,
        EndpointType.REQUIREMENT,
        EndpointType.AMBIGUITY,
        *METHOD_ELEMENTS,
    }
)
METHOD_ANCHORS: Final = frozenset({EndpointType.METHOD_MODEL, *METHOD_ELEMENTS})
COGNITIVE_MODEL_MEMBERS: Final = frozenset(
    {
        EndpointType.CLAIM,
        EndpointType.ASSUMPTION,
        EndpointType.KNOWLEDGE_GAP,
        EndpointType.REQUIREMENT,
        EndpointType.AMBIGUITY,
        EndpointType.HUMAN_JUDGMENT,
    }
)


@dataclass(frozen=True, slots=True)
class RelationshipRule:
    source_types: frozenset[EndpointType]
    relationship_type: RelationshipType
    target_types: frozenset[EndpointType]
    inverse_name: str
    cardinality: Cardinality
    review_policy: ReviewPolicy
    staleness_effect: StalenessEffect
    storage_strategy: StorageStrategy


def _rule(
    sources: EndpointType | frozenset[EndpointType],
    relation: RelationshipType,
    targets: EndpointType | frozenset[EndpointType],
    inverse: str,
    cardinality: Cardinality,
    review: ReviewPolicy,
    staleness: StalenessEffect,
    storage: StorageStrategy,
) -> RelationshipRule:
    return RelationshipRule(
        source_types=frozenset({sources}) if isinstance(sources, EndpointType) else sources,
        relationship_type=relation,
        target_types=frozenset({targets}) if isinstance(targets, EndpointType) else targets,
        inverse_name=inverse,
        cardinality=cardinality,
        review_policy=review,
        staleness_effect=staleness,
        storage_strategy=storage,
    )


RELATIONSHIP_REGISTRY: Final = (
    _rule(
        EndpointType.RESEARCH_QUESTION,
        RelationshipType.ADDRESSED_BY,
        EndpointType.METHOD_MODEL,
        "addresses",
        Cardinality.ONE_CURRENT_PER_SOURCE,
        ReviewPolicy.GROUPED_SECTION_REVIEW,
        StalenessEffect.QUESTION_REFINEMENT,
        StorageStrategy.METHOD_MODEL_FOREIGN_KEY,
    ),
    _rule(
        EndpointType.RESEARCH_QUESTION,
        RelationshipType.EVIDENCED_BY,
        EndpointType.SOURCE,
        "evidence_for",
        Cardinality.MANY_TO_MANY,
        ReviewPolicy.USER_CONFIRMED,
        StalenessEffect.SOURCE_ROLE_REVISION,
        StorageStrategy.RESEARCH_QUESTION_SOURCE,
    ),
    _rule(
        EndpointType.SOURCE,
        RelationshipType.IMPLEMENTED_BY,
        EndpointType.REPOSITORY,
        "implementation_of",
        Cardinality.AT_MOST_ONE_CURRENT,
        ReviewPolicy.USER_CONFIRMED,
        StalenessEffect.REPOSITORY_ROLE_REVISION,
        StorageStrategy.PAPER_REPOSITORY_RELATIONSHIP,
    ),
    _rule(
        EndpointType.SOURCE_CHUNK,
        RelationshipType.SUPPORTS,
        EVIDENCE_TARGETS,
        "supported_by",
        Cardinality.MANY_TO_MANY,
        ReviewPolicy.BUNDLED_WITH_CANDIDATE,
        StalenessEffect.EVIDENCE_CHANGE,
        StorageStrategy.EVIDENCE_LINK,
    ),
    _rule(
        EndpointType.SOURCE_CHUNK,
        RelationshipType.QUALIFIES,
        EVIDENCE_TARGETS,
        "qualified_by_evidence",
        Cardinality.MANY_TO_MANY,
        ReviewPolicy.BUNDLED_WITH_CANDIDATE,
        StalenessEffect.EVIDENCE_CHANGE,
        StorageStrategy.EVIDENCE_LINK,
    ),
    _rule(
        EndpointType.SOURCE_CHUNK,
        RelationshipType.CONTRADICTS,
        frozenset(
            {
                EndpointType.CLAIM,
                EndpointType.ASSUMPTION,
                EndpointType.REQUIREMENT,
                *METHOD_ELEMENTS,
            }
        ),
        "contradicted_by",
        Cardinality.MANY_TO_MANY,
        ReviewPolicy.EXPLICIT_WHEN_MATERIAL,
        StalenessEffect.EVIDENCE_CONTRADICTION,
        StorageStrategy.EVIDENCE_LINK,
    ),
    _rule(
        EndpointType.REPOSITORY_COMPONENT,
        RelationshipType.SUPPORTS,
        EVIDENCE_TARGETS,
        "supported_by",
        Cardinality.MANY_TO_MANY,
        ReviewPolicy.MAPPING_LEVEL,
        StalenessEffect.SNAPSHOT_CHANGE,
        StorageStrategy.EVIDENCE_LINK,
    ),
    _rule(
        EndpointType.REPOSITORY_COMPONENT,
        RelationshipType.QUALIFIES,
        EVIDENCE_TARGETS,
        "qualified_by_evidence",
        Cardinality.MANY_TO_MANY,
        ReviewPolicy.MAPPING_LEVEL,
        StalenessEffect.SNAPSHOT_CHANGE,
        StorageStrategy.EVIDENCE_LINK,
    ),
    _rule(
        EndpointType.REPOSITORY_COMPONENT,
        RelationshipType.CONTRADICTS,
        EVIDENCE_TARGETS,
        "contradicted_by",
        Cardinality.MANY_TO_MANY,
        ReviewPolicy.MAPPING_LEVEL,
        StalenessEffect.SNAPSHOT_CHANGE,
        StorageStrategy.EVIDENCE_LINK,
    ),
    _rule(
        EndpointType.METHOD_MODEL,
        RelationshipType.SUPPORTED_BY,
        EndpointType.CLAIM,
        "supports_model",
        Cardinality.MANY_TO_MANY,
        ReviewPolicy.METHOD_MODEL_REVIEW,
        StalenessEffect.SOURCE_REVISION,
        StorageStrategy.OBJECT_LINK,
    ),
    _rule(
        EndpointType.METHOD_MODEL,
        RelationshipType.DEPENDS_ON,
        EndpointType.ASSUMPTION,
        "assumption_for_method",
        Cardinality.MANY_TO_MANY,
        ReviewPolicy.METHOD_MODEL_REVIEW,
        StalenessEffect.SOURCE_REVISION,
        StorageStrategy.OBJECT_LINK,
    ),
    _rule(
        EndpointType.METHOD_MODEL,
        RelationshipType.CONTAINS,
        METHOD_ELEMENTS,
        "contained_in",
        Cardinality.ONE_TO_MANY,
        ReviewPolicy.GROUPED_SECTION_REVIEW,
        StalenessEffect.MODEL_COMPOSITION,
        StorageStrategy.METHOD_MODEL_MEMBER,
    ),
    _rule(
        EndpointType.EQUATION,
        RelationshipType.USES,
        frozenset({EndpointType.METHOD_SYMBOL_VARIABLE, EndpointType.METHOD_SYMBOL_PARAMETER}),
        "used_by",
        Cardinality.MANY_TO_MANY,
        ReviewPolicy.GROUPED_SECTION_REVIEW,
        StalenessEffect.EITHER_REVISION,
        StorageStrategy.OBJECT_LINK,
    ),
    _rule(
        EndpointType.METHOD_MODEL,
        RelationshipType.OPERATIONALIZED_BY,
        EndpointType.ALGORITHM_STEP,
        "operationalizes",
        Cardinality.ONE_TO_MANY,
        ReviewPolicy.GROUPED_SECTION_REVIEW,
        StalenessEffect.MODEL_COMPOSITION,
        StorageStrategy.METHOD_MODEL_MEMBER,
    ),
    _rule(
        EndpointType.ALGORITHM_STEP,
        RelationshipType.CONSUMES,
        frozenset(
            {
                EndpointType.METHOD_COMPONENT,
                EndpointType.METHOD_SYMBOL_VARIABLE,
                EndpointType.METHOD_SYMBOL_PARAMETER,
            }
        ),
        "consumed_by",
        Cardinality.MANY_TO_MANY,
        ReviewPolicy.GROUPED_SECTION_REVIEW,
        StalenessEffect.TARGET_REVISION,
        StorageStrategy.METHOD_DATA_FLOW,
    ),
    _rule(
        EndpointType.ALGORITHM_STEP,
        RelationshipType.PRODUCES,
        frozenset(
            {
                EndpointType.METHOD_COMPONENT,
                EndpointType.METHOD_SYMBOL_VARIABLE,
                EndpointType.METHOD_SYMBOL_PARAMETER,
            }
        ),
        "produced_by",
        Cardinality.MANY_TO_MANY,
        ReviewPolicy.GROUPED_SECTION_REVIEW,
        StalenessEffect.TARGET_REVISION,
        StorageStrategy.METHOD_DATA_FLOW,
    ),
    _rule(
        EndpointType.KNOWLEDGE_GAP,
        RelationshipType.CONCERNS,
        METHOD_ANCHORS,
        "concern_of",
        Cardinality.MANY_TO_MANY,
        ReviewPolicy.EXPLICIT_WHEN_MATERIAL,
        StalenessEffect.SOURCE_REVISION,
        StorageStrategy.OBJECT_LINK,
    ),
    _rule(
        EndpointType.AMBIGUITY,
        RelationshipType.AFFECTS,
        METHOD_ANCHORS,
        "affected_by",
        Cardinality.MANY_TO_MANY,
        ReviewPolicy.EXPLICIT_WHEN_MATERIAL,
        StalenessEffect.AMBIGUITY_BLOCKING,
        StorageStrategy.OBJECT_LINK,
    ),
    _rule(
        EndpointType.REQUIREMENT,
        RelationshipType.DERIVED_FROM,
        METHOD_ANCHORS,
        "source_requirement",
        Cardinality.MANY_TO_MANY,
        ReviewPolicy.BUNDLED_WITH_CANDIDATE,
        StalenessEffect.SOURCE_REVISION,
        StorageStrategy.OBJECT_LINK,
    ),
    _rule(
        EndpointType.CLAIM,
        RelationshipType.DEPENDS_ON,
        EndpointType.ASSUMPTION,
        "assumption_for",
        Cardinality.MANY_TO_MANY,
        ReviewPolicy.EXPLICIT_WHEN_MATERIAL,
        StalenessEffect.SOURCE_REVISION,
        StorageStrategy.OBJECT_LINK,
    ),
    _rule(
        EndpointType.CLAIM,
        RelationshipType.QUALIFIES,
        EndpointType.CLAIM,
        "qualified_by",
        Cardinality.MANY_TO_MANY,
        ReviewPolicy.EXPLICIT_WHEN_MATERIAL,
        StalenessEffect.EITHER_REVISION,
        StorageStrategy.OBJECT_LINK,
    ),
    _rule(
        EndpointType.REPOSITORY_COMPONENT,
        RelationshipType.IMPLEMENTS,
        frozenset(
            {
                EndpointType.REQUIREMENT,
                EndpointType.METHOD_MODEL,
                EndpointType.METHOD_COMPONENT,
                EndpointType.EQUATION,
                EndpointType.ALGORITHM_STEP,
                EndpointType.DATA_FLOW,
                EndpointType.VALIDITY_CONDITION,
            }
        ),
        "implemented_by",
        Cardinality.MANY_TO_MANY,
        ReviewPolicy.ALWAYS_REVIEWABLE,
        StalenessEffect.SNAPSHOT_CHANGE,
        StorageStrategy.PAPER_CODE_MAPPING,
    ),
    _rule(
        EndpointType.REPOSITORY_COMPONENT,
        RelationshipType.CONFIGURES,
        EndpointType.METHOD_SYMBOL_PARAMETER,
        "configured_by",
        Cardinality.MANY_TO_MANY,
        ReviewPolicy.ALWAYS_REVIEWABLE,
        StalenessEffect.SNAPSHOT_CHANGE,
        StorageStrategy.PAPER_CODE_MAPPING,
    ),
    _rule(
        EndpointType.REPOSITORY_COMPONENT,
        RelationshipType.CONTRADICTS,
        METHOD_ANCHORS,
        "contradicted_by",
        Cardinality.MANY_TO_MANY,
        ReviewPolicy.ALWAYS_REVIEWABLE,
        StalenessEffect.EVIDENCE_CONTRADICTION,
        StorageStrategy.PAPER_CODE_MAPPING,
    ),
    _rule(
        EndpointType.REPOSITORY_COMPONENT,
        RelationshipType.MAPS_TO,
        EndpointType.CLAIM,
        "mapped_from",
        Cardinality.MANY_TO_MANY,
        ReviewPolicy.ALWAYS_REVIEWABLE,
        StalenessEffect.SNAPSHOT_CHANGE,
        StorageStrategy.PAPER_CODE_MAPPING,
    ),
    _rule(
        METHOD_ANCHORS,
        RelationshipType.ABSENT_FROM_REPO,
        EndpointType.REPOSITORY_SNAPSHOT,
        "missing_anchor",
        Cardinality.MANY_TO_MANY,
        ReviewPolicy.ALWAYS_REVIEWABLE,
        StalenessEffect.SNAPSHOT_CHANGE,
        StorageStrategy.PAPER_CODE_MAPPING,
    ),
    _rule(
        EndpointType.KNOWLEDGE_GAP,
        RelationshipType.REVEALS,
        EndpointType.AMBIGUITY,
        "revealed_by",
        Cardinality.MANY_TO_MANY,
        ReviewPolicy.EXPLICIT_WHEN_MATERIAL,
        StalenessEffect.SOURCE_REVISION,
        StorageStrategy.OBJECT_LINK,
    ),
    _rule(
        EndpointType.HUMAN_JUDGMENT,
        RelationshipType.RESOLVES,
        EndpointType.AMBIGUITY,
        "resolved_by",
        Cardinality.ONE_CURRENT_PER_SOURCE,
        ReviewPolicy.HUMAN_REVIEWED,
        StalenessEffect.JUDGMENT_REVISION,
        StorageStrategy.JUDGMENT_FOREIGN_KEYS,
    ),
    _rule(
        EndpointType.IMPLEMENTATION_STEP,
        RelationshipType.SATISFIES,
        EndpointType.REQUIREMENT,
        "satisfied_by",
        Cardinality.MANY_TO_MANY,
        ReviewPolicy.BLUEPRINT_REVIEW,
        StalenessEffect.EITHER_REVISION,
        StorageStrategy.OBJECT_LINK,
    ),
    _rule(
        EndpointType.VERIFICATION_CHECK,
        RelationshipType.TESTS,
        EndpointType.IMPLEMENTATION_STEP,
        "tested_by",
        Cardinality.MANY_TO_ONE,
        ReviewPolicy.BLUEPRINT_REVIEW,
        StalenessEffect.SOURCE_REVISION,
        StorageStrategy.VERIFICATION_CHECK_FOREIGN_KEY,
    ),
    _rule(
        EndpointType.VERIFICATION_CHECK,
        RelationshipType.VALIDATES,
        frozenset({EndpointType.REQUIREMENT, *METHOD_ANCHORS}),
        "validated_by",
        Cardinality.MANY_TO_MANY,
        ReviewPolicy.BLUEPRINT_REVIEW,
        StalenessEffect.SOURCE_REVISION,
        StorageStrategy.OBJECT_LINK_AND_DEPENDENCY,
    ),
    _rule(
        EndpointType.MENTAL_MODEL,
        RelationshipType.INCLUDES,
        frozenset({*COGNITIVE_MODEL_MEMBERS, EndpointType.EVIDENCE_LINK}),
        "included_in",
        Cardinality.MANY_TO_MANY,
        ReviewPolicy.MODEL_COMPOSITION,
        StalenessEffect.EITHER_REVISION,
        StorageStrategy.MENTAL_MODEL_MEMBER,
    ),
    _rule(
        EndpointType.MENTAL_MODEL,
        RelationshipType.INCLUDES_METHOD,
        EndpointType.METHOD_MODEL,
        "method_for_model",
        Cardinality.MANY_TO_ONE,
        ReviewPolicy.AUTOMATIC,
        StalenessEffect.MODEL_REVISION,
        StorageStrategy.MENTAL_MODEL_METHOD_FOREIGN_KEY,
    ),
    _rule(
        EndpointType.MENTAL_MODEL,
        RelationshipType.ADDRESSES,
        EndpointType.RESEARCH_QUESTION,
        "addressed_by_model",
        Cardinality.MANY_TO_ONE,
        ReviewPolicy.AUTOMATIC,
        StalenessEffect.QUESTION_REFINEMENT,
        StorageStrategy.MENTAL_MODEL_QUESTION_FOREIGN_KEY,
    ),
    _rule(
        EndpointType.BLUEPRINT,
        RelationshipType.DERIVED_FROM,
        EndpointType.MENTAL_MODEL,
        "source_of",
        Cardinality.MANY_TO_ONE,
        ReviewPolicy.AUTOMATIC,
        StalenessEffect.MODEL_REVISION,
        StorageStrategy.BLUEPRINT_MENTAL_MODEL_FOREIGN_KEY,
    ),
)


@dataclass(frozen=True, slots=True)
class RelationshipEndpoint:
    id: UUID
    project_id: UUID
    endpoint_type: EndpointType
    version: int | None = None
    current: bool = True


@dataclass(frozen=True, slots=True)
class RelationshipRequest:
    source: RelationshipEndpoint
    relationship_type: RelationshipType
    target: RelationshipEndpoint
    storage_strategy: StorageStrategy
    review_status: KnowledgeReviewStatus
    material: bool
    materiality_policy_version: str
    objective_context_fingerprint: str
    mental_model_member_role: MentalModelMemberRole | None = None
    existing_current_count: int = 0
    existing_fingerprints: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ValidatedRelationship:
    rule: RelationshipRule
    fingerprint: str
    duplicate_no_op: bool
    usable_for_reasoning: bool


def relationship_fingerprint(request: RelationshipRequest) -> str:
    payload = {
        "policy_version": RELATIONSHIP_POLICY_VERSION,
        "source_id": str(request.source.id),
        "source_type": request.source.endpoint_type.value,
        "source_version": request.source.version,
        "relationship_type": request.relationship_type.value,
        "target_id": str(request.target.id),
        "target_type": request.target.endpoint_type.value,
        "target_version": request.target.version,
        "storage_strategy": request.storage_strategy.value,
        "material": request.material,
        "materiality_policy_version": request.materiality_policy_version,
        "objective_context_fingerprint": request.objective_context_fingerprint,
        "mental_model_member_role": (
            None
            if request.mental_model_member_role is None
            else request.mental_model_member_role.value
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class RelationshipPolicy:
    """Reject invalid direction, endpoint, storage, review, and cardinality requests."""

    @staticmethod
    def validate(request: RelationshipRequest) -> ValidatedRelationship:
        if request.source.project_id != request.target.project_id:
            raise DomainValidationError(details={"reason": "relationship_cross_project"})
        if request.source.id == request.target.id:
            raise DomainValidationError(details={"reason": "relationship_self_link"})
        if not request.source.current or not request.target.current:
            raise ResourceConflictError(details={"reason": "relationship_endpoint_not_current"})
        if request.source.version is not None and request.source.version < 1:
            raise DomainValidationError(details={"reason": "relationship_source_version_invalid"})
        if request.target.version is not None and request.target.version < 1:
            raise DomainValidationError(details={"reason": "relationship_target_version_invalid"})
        if not request.materiality_policy_version.strip():
            raise DomainValidationError(details={"reason": "materiality_policy_version_missing"})
        if len(request.objective_context_fingerprint) != 64 or any(
            character not in "0123456789abcdef"
            for character in request.objective_context_fingerprint
        ):
            raise DomainValidationError(details={"reason": "objective_context_fingerprint_invalid"})

        matches = tuple(
            rule
            for rule in RELATIONSHIP_REGISTRY
            if request.source.endpoint_type in rule.source_types
            and request.relationship_type is rule.relationship_type
            and request.target.endpoint_type in rule.target_types
            and request.storage_strategy is rule.storage_strategy
        )
        if len(matches) != 1:
            raise DomainValidationError(details={"reason": "relationship_registry_mismatch"})
        rule = matches[0]
        exceptional_roles = {
            MentalModelMemberRole.DECISIVE_SUPPORTING_EVIDENCE,
            MentalModelMemberRole.CENTRAL_CONTRADICTING_EVIDENCE,
        }
        is_mental_model_inclusion = (
            request.source.endpoint_type is EndpointType.MENTAL_MODEL
            and request.relationship_type is RelationshipType.INCLUDES
        )
        if is_mental_model_inclusion and request.target.endpoint_type is EndpointType.EVIDENCE_LINK:
            if request.mental_model_member_role not in exceptional_roles:
                raise DomainValidationError(
                    details={"reason": "mental_model_evidence_role_invalid"}
                )
        elif request.mental_model_member_role in exceptional_roles:
            raise DomainValidationError(details={"reason": "exceptional_evidence_role_misused"})
        elif not is_mental_model_inclusion and request.mental_model_member_role is not None:
            raise DomainValidationError(details={"reason": "mental_model_member_role_unexpected"})
        if request.existing_current_count < 0:
            raise DomainValidationError(details={"reason": "relationship_count_invalid"})
        fingerprint = relationship_fingerprint(request)
        if fingerprint in request.existing_fingerprints:
            return ValidatedRelationship(
                rule=rule,
                fingerprint=fingerprint,
                duplicate_no_op=True,
                usable_for_reasoning=request.review_status is KnowledgeReviewStatus.ACCEPTED,
            )
        if (
            rule.cardinality
            in {
                Cardinality.ONE_CURRENT_PER_SOURCE,
                Cardinality.AT_MOST_ONE_CURRENT,
            }
            and request.existing_current_count >= 1
        ):
            raise ResourceConflictError(details={"reason": "relationship_cardinality_exceeded"})
        if (
            rule.review_policy is ReviewPolicy.EXPLICIT_WHEN_MATERIAL
            and request.material
            and request.review_status
            in {KnowledgeReviewStatus.CANDIDATE, KnowledgeReviewStatus.NEEDS_REVISION}
        ):
            raise ResourceConflictError(details={"reason": "material_relationship_unreviewed"})
        if rule.review_policy in {
            ReviewPolicy.USER_CONFIRMED,
            ReviewPolicy.HUMAN_REVIEWED,
        } and request.review_status in {
            KnowledgeReviewStatus.CANDIDATE,
            KnowledgeReviewStatus.NEEDS_REVISION,
        }:
            raise ResourceConflictError(details={"reason": "relationship_confirmation_required"})
        return ValidatedRelationship(
            rule=rule,
            fingerprint=fingerprint,
            duplicate_no_op=False,
            usable_for_reasoning=request.review_status is KnowledgeReviewStatus.ACCEPTED,
        )
