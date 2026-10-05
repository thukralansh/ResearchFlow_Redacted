"""Application callbacks used by thin project-analysis graph nodes."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from researchflow.workflows.state import ProjectWorkflowState, WorkflowStage


@dataclass(frozen=True, slots=True)
class PropositionExtractionOutcome:
    complete: bool
    continuation_cursor: str | None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class MethodExtractionOutcome:
    method_model_id: UUID | None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class ScientificReviewOutcome:
    interrupt_id: UUID
    context_fingerprint: str


@dataclass(frozen=True, slots=True)
class AcceptedReviewOutcome:
    accepted_method_model_id: UUID | None
    accepted_proposition_count: int
    error_code: str | None = None


class WorkflowNodeContext(Protocol):
    """Commit product-visible lifecycle state before a node returns to LangGraph."""

    async def commit_stage(
        self, state: ProjectWorkflowState, stage: WorkflowStage
    ) -> ProjectWorkflowState: ...

    async def validate_inputs(self, state: ProjectWorkflowState) -> None: ...
    async def validate_primary_source(self, state: ProjectWorkflowState) -> None: ...
    async def discover_repository(self, state: ProjectWorkflowState) -> UUID | None: ...
    async def extract_propositions(
        self, state: ProjectWorkflowState
    ) -> PropositionExtractionOutcome: ...
    async def extract_method_model(
        self, state: ProjectWorkflowState
    ) -> MethodExtractionOutcome: ...
    async def validate_grounding(self, state: ProjectWorkflowState) -> None: ...
    async def prepare_scientific_review(
        self, state: ProjectWorkflowState
    ) -> ScientificReviewOutcome: ...
    async def validate_completed_review(
        self, state: ProjectWorkflowState
    ) -> AcceptedReviewOutcome: ...
    async def compose_initial_mental_model(self, state: ProjectWorkflowState) -> UUID: ...
