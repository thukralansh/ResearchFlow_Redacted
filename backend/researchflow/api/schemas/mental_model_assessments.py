"""Strict  post-mapping Mental Model reassessment contracts."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from researchflow.api.schemas.mental_models import MentalModelMemberCandidate


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MentalModelTargetReference(_StrictModel):
    target_type: Literal["knowledge_object", "human_judgment", "evidence_link"]
    target_id: UUID


class MentalModelReassessmentParagraph(_StrictModel):
    member_targets: Annotated[list[MentalModelTargetReference], Field(min_length=1)]
    content: Annotated[str, Field(min_length=1, max_length=20_000)]


class MentalModelReassessmentBatch(_StrictModel):
    result: Literal["no_change", "new_version_created"]
    rationale: Annotated[str, Field(min_length=1, max_length=4_000)]
    authority_disclosure: Annotated[str, Field(min_length=1, max_length=4_000)]
    members: Annotated[list[MentalModelMemberCandidate], Field(min_length=1, max_length=128)]
    rendering: list[MentalModelReassessmentParagraph] | None = None

    @model_validator(mode="after")
    def exact_result_shape(self) -> "MentalModelReassessmentBatch":
        if self.result == "new_version_created" and not self.rendering:
            raise ValueError("mental_model_reassessment_rendering_required")
        if self.result == "no_change" and self.rendering is not None:
            raise ValueError("mental_model_reassessment_rendering_unexpected")
        targets = [(item.target_type, item.target_id) for item in self.members]
        if len(targets) != len(set(targets)):
            raise ValueError("mental_model_member_duplicate")
        return self


class MentalModelReassessmentRequest(_StrictModel):
    workflow_run_id: UUID
    mapping_review_gate_event_id: UUID
    method_model_reassessment_event_id: UUID
    applicable: bool = True
    explicit_rerun: bool = False


class MentalModelMemberChange(_StrictModel):
    target_type: Literal["knowledge_object", "human_judgment", "evidence_link"]
    target_id: UUID


class MentalModelAssessmentRecordResponse(_StrictModel):
    id: UUID
    project_id: UUID
    assessed_model_id: UUID
    repository_snapshot_id: UUID | None
    research_question_id: UUID
    method_model_id: UUID
    method_model_assessment_id: UUID
    method_model_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    repository_evidence_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    accepted_knowledge_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    accepted_mapping_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    current_judgment_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    result: Literal["no_change", "new_version_created"]
    resulting_model_id: UUID | None
    rationale: str
    generation_record_id: UUID | None


class MentalModelReassessmentResponse(_StrictModel):
    outcome: Literal["not_applicable", "no_change", "new_version_created"]
    generation_record_id: UUID | None
    assessment: MentalModelAssessmentRecordResponse
    prior_mental_model_id: UUID
    resulting_mental_model_id: UUID
    mapping_review_gate_event_id: UUID
    method_model_reassessment_event_id: UUID
    event_id: UUID
    replayed: bool
    changed_members: list[MentalModelMemberChange]
    unchanged_members: list[MentalModelMemberChange]
    rationale: str
    authority_disclosure: str | None


class MentalModelAssessmentCoverageResponse(_StrictModel):
    current_mental_model_id: UUID
    covered: bool
    assessment: MentalModelAssessmentRecordResponse | None
    reason: str
