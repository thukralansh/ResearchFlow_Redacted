"""Register canonical persistence records with shared SQLAlchemy metadata."""

from researchflow.infrastructure.database.models.audit import OperationEventRecord
from researchflow.infrastructure.database.models.blueprint import (
    BlueprintEligibilityAssessmentRecord,
    BlueprintEligibilityItemRecord,
    BlueprintSectionRecord,
    ImplementationBlueprintRecord,
    ImplementationStepRecord,
    VerificationCheckRecord,
)
from researchflow.infrastructure.database.models.copilot import (
    CopilotActionConfirmationRecord,
    CopilotActionProposalRecord,
    CopilotManifestRecord,
    CopilotMessageRecord,
    CopilotThreadRecord,
    CopilotTurnRecord,
)
from researchflow.infrastructure.database.models.dependency import (
    ArtifactDependencyRecord,
    ArtifactInvalidationRecord,
)
from researchflow.infrastructure.database.models.evidence import (
    EvidenceLinkRecord,
    GroundingAssessmentRecord,
)
from researchflow.infrastructure.database.models.generation import GenerationRecord
from researchflow.infrastructure.database.models.judgment import (
    HumanJudgmentRecord,
    JudgmentOptionRecord,
    JudgmentRequestRecord,
)
from researchflow.infrastructure.database.models.knowledge import (
    AmbiguityInterpretationRecord,
    AmbiguityRecord,
    AssumptionRecord,
    ClaimRecord,
    ImplementationRequirementRecord,
    KnowledgeGapRecord,
    KnowledgeObjectPresentationRecord,
    KnowledgeObjectRecord,
)
from researchflow.infrastructure.database.models.mapping import PaperCodeMappingRecord
from researchflow.infrastructure.database.models.mental_model import (
    MentalModelAssessmentRecord,
    MentalModelMemberRecord,
    MentalModelRecord,
    MentalModelRenderingRecord,
)
from researchflow.infrastructure.database.models.method_element import (
    MethodAlgorithmStepRecord,
    MethodComponentRecord,
    MethodDataFlowRecord,
    MethodElementRecord,
    MethodEquationRecord,
    MethodSymbolRecord,
    MethodValidityConditionRecord,
)
from researchflow.infrastructure.database.models.method_model import (
    MethodModelAssessmentRecord,
    MethodModelMemberRecord,
    MethodModelRecord,
    MethodModelRenderingRecord,
    MethodModelSectionReviewRecord,
)
from researchflow.infrastructure.database.models.project import ProjectRecord
from researchflow.infrastructure.database.models.relationship import ObjectLinkRecord
from researchflow.infrastructure.database.models.repository import (
    PaperRepositoryRelationshipRecord,
    RepositoryComponentRecord,
    RepositoryDiscoveryRunRecord,
    RepositoryInspectionRunRecord,
    RepositoryRecord,
    RepositorySnapshotRecord,
    RepositoryTreeEntryRecord,
)
from researchflow.infrastructure.database.models.research_question import (
    ResearchQuestionRecord,
    ResearchQuestionSourceRecord,
)
from researchflow.infrastructure.database.models.source import (
    SourceChunkRecord,
    SourceProcessingRunRecord,
    SourceRecord,
)
from researchflow.infrastructure.database.models.workflow import (
    WorkflowInterruptRecord,
    WorkflowRunRecord,
)

__all__ = [
    "AmbiguityInterpretationRecord",
    "AmbiguityRecord",
    "ArtifactDependencyRecord",
    "ArtifactInvalidationRecord",
    "AssumptionRecord",
    "BlueprintEligibilityAssessmentRecord",
    "BlueprintEligibilityItemRecord",
    "BlueprintSectionRecord",
    "ClaimRecord",
    "CopilotActionConfirmationRecord",
    "CopilotActionProposalRecord",
    "CopilotManifestRecord",
    "CopilotMessageRecord",
    "CopilotThreadRecord",
    "CopilotTurnRecord",
    "EvidenceLinkRecord",
    "GenerationRecord",
    "GroundingAssessmentRecord",
    "HumanJudgmentRecord",
    "ImplementationBlueprintRecord",
    "ImplementationRequirementRecord",
    "ImplementationStepRecord",
    "JudgmentOptionRecord",
    "JudgmentRequestRecord",
    "KnowledgeGapRecord",
    "KnowledgeObjectPresentationRecord",
    "KnowledgeObjectRecord",
    "MentalModelAssessmentRecord",
    "MentalModelMemberRecord",
    "MentalModelRecord",
    "MentalModelRenderingRecord",
    "MethodAlgorithmStepRecord",
    "MethodComponentRecord",
    "MethodDataFlowRecord",
    "MethodElementRecord",
    "MethodEquationRecord",
    "MethodModelAssessmentRecord",
    "MethodModelMemberRecord",
    "MethodModelRecord",
    "MethodModelRenderingRecord",
    "MethodModelSectionReviewRecord",
    "MethodSymbolRecord",
    "MethodValidityConditionRecord",
    "ObjectLinkRecord",
    "OperationEventRecord",
    "PaperCodeMappingRecord",
    "PaperRepositoryRelationshipRecord",
    "ProjectRecord",
    "RepositoryComponentRecord",
    "RepositoryDiscoveryRunRecord",
    "RepositoryInspectionRunRecord",
    "RepositoryRecord",
    "RepositorySnapshotRecord",
    "RepositoryTreeEntryRecord",
    "ResearchQuestionRecord",
    "ResearchQuestionSourceRecord",
    "SourceChunkRecord",
    "SourceProcessingRunRecord",
    "SourceRecord",
    "VerificationCheckRecord",
    "WorkflowInterruptRecord",
    "WorkflowRunRecord",
]
