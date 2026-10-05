"""Typed Copilot proposal, preview, confirmation, and recoverable execution."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from researchflow.application.blueprint_review import (
    AcceptBlueprintCommand,
    BlueprintReviewService,
    ReviseBlueprintCommand,
)
from researchflow.application.candidate_creation import CandidateCreationService
from researchflow.application.knowledge_review import (
    EvidenceReviewCommand,
    KnowledgeReviewService,
    ReviewTransitionCommand,
    SemanticRevisionCommand,
)
from researchflow.application.mapping_review import (
    MappingReviewService,
    MappingRevisionCommand,
    MappingTransitionCommand,
)
from researchflow.application.method_review import (
    AcceptMethodModelCommand,
    MethodElementRevisionCommand,
    MethodElementTransitionCommand,
    MethodEvidenceReviewCommand,
    MethodReviewService,
)
from researchflow.application.staleness import DirectStalenessCommand, StalenessService
from researchflow.application.verification import (
    RecordVerificationResultCommand,
    VerificationService,
)
from researchflow.domain.actor import ActorContext
from researchflow.domain.copilot import CopilotProposalStatus
from researchflow.domain.copilot_actions import (
    COPILOT_ACTION_POLICY_VERSION,
    COPILOT_ACTION_SCHEMA_VERSION,
    COPILOT_PREVIEW_POLICY_VERSION,
    BlueprintReviewPayload,
    ConfirmationClass,
    CopilotActionType,
    CopilotProposedAction,
    EvidenceCandidatePayload,
    EvidenceReviewPayload,
    HumanJudgmentPayload,
    KnowledgeCandidatePayload,
    KnowledgeRevisionPayload,
    MappingReviewPayload,
    MappingRevisionPayload,
    MethodElementReviewPayload,
    MethodElementRevisionPayload,
    MethodEvidenceReviewPayload,
    MethodModelReviewPayload,
    ReviewTargetPayload,
    SpecializedIngestionProposalPayload,
    VerificationResultPayload,
    confirmation_class_for,
    validate_action_payload,
    validated_knowledge_payload,
)
from researchflow.domain.method_catalog import MethodElementType, MethodEpistemicStatus
from researchflow.domain.model_catalog import PaperAnchorType, PaperCodeMappingType
from researchflow.errors import (
    DomainValidationError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from researchflow.ports.copilot import (
    CopilotActionConfirmationData,
    CopilotActionProposalData,
    CopilotManifestData,
    CopilotTurnData,
    NewCopilotActionConfirmation,
    NewCopilotActionProposal,
)
from researchflow.ports.providers import StructuredGenerationModelPolicy
from researchflow.ports.repositories import OperationEventData
from researchflow.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory

_NAMESPACE = uuid5(NAMESPACE_URL, "https://researchflow.local/copilot-actions-v1")


@dataclass(frozen=True, slots=True)
class CopilotImpactPreview:
    policy_version: str
    scope: str
    complete: bool
    warning: str
    expected_state: dict[str, object]
    effects: tuple[dict[str, object], ...]
    fingerprint: str


@dataclass(frozen=True, slots=True)
class ProposedActionResult:
    proposal: CopilotActionProposalData
    preview: CopilotImpactPreview
    replayed: bool


@dataclass(frozen=True, slots=True)
class ConfirmActionCommand:
    project_id: UUID
    proposal_id: UUID
    command_id: UUID
    expected_proposal_fingerprint: str
    expected_preview_fingerprint: str
    rationale: str | None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class ConfirmedActionResult:
    proposal: CopilotActionProposalData
    confirmation: CopilotActionConfirmationData
    outcome_event: OperationEventData
    replayed: bool


class HumanJudgmentExecutor(Protocol):
    async def execute(
        self,
        *,
        payload: HumanJudgmentPayload,
        project_id: UUID,
        command_id: UUID,
        rationale: str,
        request_id: str | None,
    ) -> OperationEventData: ...


class ExistingHumanJudgmentAdapter:
    """Keep model-authored drafts separate from the human confirmation rationale."""

    def __init__(self, *, service: object, uow_factory: UnitOfWorkFactory) -> None:
        self._service = service
        self._uow_factory = uow_factory

    async def execute(
        self,
        *,
        payload: HumanJudgmentPayload,
        project_id: UUID,
        command_id: UUID,
        rationale: str,
        request_id: str | None,
    ) -> OperationEventData:
        from researchflow.application.judgments import HumanJudgmentService, ResolveJudgmentCommand

        if not isinstance(self._service, HumanJudgmentService):
            raise ResourceConflictError(details={"reason": "human_judgment_executor_missing"})
        await self._service.resolve(
            ResolveJudgmentCommand(
                project_id=project_id,
                request_id=payload.judgment_request_id,
                workflow_run_id=payload.workflow_run_id,
                command_id=command_id,
                selected_option_id=payload.selected_option_id,
                custom_decision=payload.proposed_custom_decision,
                rationale=rationale,
                confidence=payload.confidence,
                method_model_affected=payload.method_model_affected,
                method_impact_rationale=payload.method_impact_rationale,
                mapping_review_gate_event_id=payload.mapping_review_gate_event_id,
                repository_applicable=payload.repository_applicable,
                confirmed=True,
                expected_current_judgment_id=payload.expected_current_judgment_id,
            )
        )
        async with self._uow_factory(project_id) as uow:
            event = await uow.events.get_by_idempotency_key(
                "human_judgment.recorded", f"human-judgment-command:{command_id}"
            )
            await uow.rollback()
        if event is None:
            raise ResourceConflictError(details={"reason": "copilot_action_outcome_missing"})
        return event


class CopilotActionService:
    """Persist model proposals first; execute only after server-authoritative confirmation."""

    def __init__(
        self,
        *,
        actor: ActorContext,
        uow_factory: UnitOfWorkFactory,
        judgment_executor: HumanJudgmentExecutor | None = None,
        blueprint_service: BlueprintReviewService | None = None,
        blueprint_model_policy: StructuredGenerationModelPolicy | None = None,
    ) -> None:
        self._actor = actor
        self._uow_factory = uow_factory
        self._staleness = StalenessService(actor=actor, uow_factory=uow_factory)
        self._candidates = CandidateCreationService(actor=actor, uow_factory=uow_factory)
        self._knowledge = KnowledgeReviewService(
            actor=actor,
            uow_factory=uow_factory,
            staleness_hook=self._staleness,
        )
        self._verification = VerificationService(actor=actor, uow_factory=uow_factory)
        self._judgments = judgment_executor
        self._methods = MethodReviewService(
            actor=actor, uow_factory=uow_factory, staleness_hook=self._staleness
        )
        self._mappings = MappingReviewService(
            actor=actor, uow_factory=uow_factory, staleness_hook=self._staleness
        )
        self._blueprints = blueprint_service
        self._blueprint_policy = blueprint_model_policy

    async def record_generated(
        self,
        *,
        project_id: UUID,
        turn: CopilotTurnData,
        generation_record_id: UUID,
        actions: tuple[CopilotProposedAction, ...],
        context_manifest: CopilotManifestData,
        retrieval_manifest: CopilotManifestData | None,
    ) -> tuple[ProposedActionResult, ...]:
        manifests = {context_manifest.id: context_manifest}
        if retrieval_manifest is not None:
            manifests[retrieval_manifest.id] = retrieval_manifest
        results: list[ProposedActionResult] = []
        for index, action in enumerate(actions):
            manifest = manifests.get(action.manifest_id)
            if manifest is None:
                raise DomainValidationError(details={"reason": "copilot_action_manifest_invalid"})
            payload = validate_action_payload(action)
            referenced = _payload_ids(payload.model_dump(mode="json"))
            chain: tuple[CopilotManifestData, ...] = (manifest,)
            if manifest.parent_manifest_id is not None:
                parent = manifests.get(manifest.parent_manifest_id)
                if parent is None:
                    raise DomainValidationError(
                        details={"reason": "copilot_action_manifest_parent_invalid"}
                    )
                chain = (parent, manifest)
            allowed = set().union(*(_manifest_reference_ids(item.ordered_items) for item in chain))
            if not referenced.issubset(allowed):
                raise DomainValidationError(
                    details={"reason": "copilot_action_target_not_in_manifest"}
                )
            expected = await self._expected_state(project_id, chain, referenced)
            preview = await self._preview(project_id, action.action_type, payload, expected)
            command_id = uuid5(
                _NAMESPACE,
                f"proposal:{generation_record_id}:{index}:{_fingerprint(action.model_dump(mode='json'))}",
            )
            result = await self._record_proposal(
                project_id=project_id,
                turn=turn,
                generation_record_id=generation_record_id,
                command_id=command_id,
                action=action,
                expected=expected,
                preview=preview,
            )
            results.append(result)
        return tuple(results)

    async def preview(self, project_id: UUID, proposal_id: UUID) -> ProposedActionResult:
        proposal, action, payload, manifests = await self._load(project_id, proposal_id)
        expected = await self._expected_state(
            project_id,
            manifests,
            _payload_ids(action.payload.model_dump(mode="json", exclude_none=False)),
        )
        preview = await self._preview(project_id, action.action_type, payload, expected)
        if (
            expected != proposal.expected_state
            or preview.fingerprint != proposal.impact_preview_fingerprint
        ):
            await self._invalidate(project_id, proposal, "proposal_target_or_preview_changed")
            raise ResourceConflictError(details={"reason": "copilot_proposal_invalidated"})
        return ProposedActionResult(proposal, preview, True)

    async def confirm(self, command: ConfirmActionCommand) -> ConfirmedActionResult:
        proposal, action, payload, manifests = await self._load(
            command.project_id, command.proposal_id
        )
        if proposal.input_fingerprint != command.expected_proposal_fingerprint:
            raise ResourceConflictError(details={"reason": "copilot_proposal_fingerprint_changed"})
        if proposal.impact_preview_fingerprint != command.expected_preview_fingerprint:
            raise ResourceConflictError(details={"reason": "copilot_preview_fingerprint_changed"})
        if confirmation_class_for(action.action_type) is ConfirmationClass.SPECIALIZED_HANDOFF:
            raise ResourceConflictError(
                details={"reason": "specialized_ingestion_handoff_required"}
            )
        rationale = None if command.rationale is None else command.rationale.strip()
        if confirmation_class_for(action.action_type) is ConfirmationClass.STRONG:
            if not rationale or len(rationale) > 4_000:
                raise DomainValidationError(
                    details={"reason": "copilot_confirmation_rationale_invalid"}
                )
        elif rationale is not None and (not rationale or len(rationale) > 4_000):
            raise DomainValidationError(
                details={"reason": "copilot_confirmation_rationale_invalid"}
            )
        expected = await self._expected_state(
            command.project_id,
            manifests,
            _payload_ids(action.payload.model_dump(mode="json", exclude_none=False)),
        )
        preview = await self._preview(command.project_id, action.action_type, payload, expected)
        if (
            expected != proposal.expected_state
            or preview.fingerprint != command.expected_preview_fingerprint
        ):
            await self._invalidate(
                command.project_id, proposal, "proposal_target_or_preview_changed"
            )
            raise ResourceConflictError(details={"reason": "copilot_proposal_invalidated"})
        confirmation, pending, confirmation_replayed = await self._confirm_pending(
            command, proposal, rationale
        )
        event = await self._find_outcome(command.project_id, pending)
        execution_id = uuid5(_NAMESPACE, f"execute:{pending.id}")
        if event is None:
            event = await self._execute(
                proposal=pending,
                action=action,
                payload=payload,
                command_id=execution_id,
                rationale=rationale,
                request_id=command.request_id,
            )
        executed = await self._mark_executed(command.project_id, pending, event)
        return ConfirmedActionResult(executed, confirmation, event, confirmation_replayed)

    async def reconcile(self, project_id: UUID, proposal_id: UUID) -> ConfirmedActionResult:
        proposal, action, payload, _ = await self._load(project_id, proposal_id)
        if proposal.status != CopilotProposalStatus.CONFIRMED_PENDING.value:
            raise ResourceConflictError(details={"reason": "copilot_proposal_not_pending_recovery"})
        confirmations = await self._confirmations(project_id, proposal.id)
        if len(confirmations) != 1:
            raise ResourceConflictError(details={"reason": "copilot_confirmation_missing"})
        event = await self._find_outcome(project_id, proposal)
        if event is None:
            event = await self._execute(
                proposal=proposal,
                action=action,
                payload=payload,
                command_id=uuid5(_NAMESPACE, f"execute:{proposal.id}"),
                rationale=confirmations[0].rationale,
                request_id=None,
            )
        executed = await self._mark_executed(project_id, proposal, event)
        return ConfirmedActionResult(executed, confirmations[0], event, True)

    async def _record_proposal(
        self,
        *,
        project_id: UUID,
        turn: CopilotTurnData,
        generation_record_id: UUID,
        command_id: UUID,
        action: CopilotProposedAction,
        expected: dict[str, object],
        preview: CopilotImpactPreview,
    ) -> ProposedActionResult:
        identity = {
            "policy_version": COPILOT_ACTION_POLICY_VERSION,
            "action_type": action.action_type.value,
            "schema_version": action.schema_version,
            "model_explanation": action.explanation,
            "payload": action.payload.model_dump(mode="json", exclude_none=False),
            "expected_state": expected,
            "context_manifest_fingerprint": expected["manifest_fingerprint"],
            "preview_fingerprint": preview.fingerprint,
            "confirmation_class": confirmation_class_for(action.action_type).value,
        }
        fingerprint = _fingerprint(identity)
        proposal_id = uuid5(_NAMESPACE, f"proposal:{command_id}")
        async with self._uow_factory(project_id) as uow:
            await self._owned(uow)
            generation = await uow.generations.get_exact(generation_record_id)
            current_turn = await uow.copilot.get_turn(turn.id)
            if (
                generation is None
                or current_turn is None
                or generation.copilot_turn_id != current_turn.id
                or generation.status != "succeeded"
            ):
                raise ResourceConflictError(details={"reason": "proposal_generation_mismatch"})
            existing = await uow.copilot.get_proposal(proposal_id)
            if existing is not None:
                if existing.input_fingerprint != fingerprint:
                    raise ResourceConflictError(details={"reason": "command_payload_changed"})
                await uow.rollback()
                return ProposedActionResult(existing, preview, True)
            proposal = await uow.copilot.add_proposal(
                NewCopilotActionProposal(
                    id=proposal_id,
                    project_id=project_id,
                    thread_id=turn.thread_id,
                    turn_id=turn.id,
                    generation_record_id=generation.id,
                    action_type=action.action_type.value,
                    schema_version=action.schema_version,
                    model_explanation=action.explanation,
                    payload=action.payload.model_dump(mode="json", exclude_none=False),
                    payload_fingerprint=_fingerprint(
                        action.payload.model_dump(mode="json", exclude_none=False)
                    ),
                    expected_state=expected,
                    expected_state_fingerprint=_fingerprint(expected),
                    context_manifest_fingerprint=str(expected["manifest_fingerprint"]),
                    confirmation_class=confirmation_class_for(action.action_type).value,
                    impact_preview_fingerprint=preview.fingerprint,
                    action_policy_version=COPILOT_ACTION_POLICY_VERSION,
                    creation_command_id=command_id,
                    input_fingerprint=fingerprint,
                )
            )
            await uow.commit()
            return ProposedActionResult(proposal, preview, False)

    async def _confirm_pending(
        self,
        command: ConfirmActionCommand,
        proposal: CopilotActionProposalData,
        rationale: str | None,
    ) -> tuple[CopilotActionConfirmationData, CopilotActionProposalData, bool]:
        confirmation_id = uuid5(_NAMESPACE, f"confirmation:{command.command_id}")
        fingerprint = _fingerprint(
            {
                "proposal_fingerprint": proposal.input_fingerprint,
                "preview_fingerprint": proposal.impact_preview_fingerprint,
                "rationale": rationale,
                "actor_id": str(self._actor.actor_id),
            }
        )
        async with self._uow_factory(command.project_id) as uow:
            await self._owned(uow)
            current = await uow.copilot.get_proposal_for_update(proposal.id)
            if current is None:
                raise ResourceNotFoundError(details={"resource": "copilot_action_proposal"})
            confirmations = await uow.copilot.list_confirmations(current.id)
            existing = next((item for item in confirmations if item.id == confirmation_id), None)
            if existing is not None:
                if existing.input_fingerprint != fingerprint:
                    raise ResourceConflictError(details={"reason": "command_payload_changed"})
                await uow.rollback()
                return existing, current, True
            if current.status != CopilotProposalStatus.PENDING.value:
                raise ResourceConflictError(details={"reason": "copilot_proposal_state_changed"})
            confirmation = await uow.copilot.add_confirmation(
                NewCopilotActionConfirmation(
                    id=confirmation_id,
                    project_id=command.project_id,
                    proposal_id=current.id,
                    actor_id=self._actor.actor_id,
                    proposal_fingerprint=current.input_fingerprint,
                    preview_fingerprint=current.impact_preview_fingerprint,
                    rationale=rationale,
                    command_id=command.command_id,
                    input_fingerprint=fingerprint,
                )
            )
            pending = await uow.copilot.transition_proposal(
                current.id,
                expected_status=CopilotProposalStatus.PENDING.value,
                status=CopilotProposalStatus.CONFIRMED_PENDING.value,
            )
            await uow.commit()
            return confirmation, pending, False

    async def _execute(
        self,
        *,
        proposal: CopilotActionProposalData,
        action: CopilotProposedAction,
        payload: object,
        command_id: UUID,
        rationale: str | None,
        request_id: str | None,
    ) -> OperationEventData:
        human_reason = (
            rationale or "Human explicitly saved this proposal as a reviewable candidate."
        )
        event_id: UUID
        if isinstance(payload, KnowledgeCandidatePayload):
            candidate_result = await self._candidates.create_knowledge_candidate(
                project_id=proposal.project_id,
                generation_record_id=proposal.generation_record_id,
                command_id=command_id,
                payload=payload,
                request_id=request_id,
            )
            event_id = candidate_result.event_id
        elif isinstance(payload, EvidenceCandidatePayload):
            evidence_result = await self._candidates.create_evidence_candidate(
                project_id=proposal.project_id,
                generation_record_id=proposal.generation_record_id,
                command_id=command_id,
                payload=payload,
                request_id=request_id,
            )
            event_id = evidence_result.event_id
        elif isinstance(payload, KnowledgeRevisionPayload):
            revision_result = await self._knowledge.create_semantic_revision(
                SemanticRevisionCommand(
                    project_id=proposal.project_id,
                    object_id=payload.target_id,
                    command_id=command_id,
                    expected_version=payload.expected_version,
                    expected_review_status=payload.expected_review_status,
                    expected_lifecycle_status=payload.expected_lifecycle_status,
                    rationale=human_reason,
                    payload=validated_knowledge_payload(payload),
                    reproposed_evidence_link_ids=payload.reproposed_evidence_link_ids,
                    request_id=request_id,
                )
            )
            event_id = revision_result.event_id
        elif action.action_type is CopilotActionType.REVIEW_KNOWLEDGE_OBJECT and isinstance(
            payload, ReviewTargetPayload
        ):
            command = ReviewTransitionCommand(
                project_id=proposal.project_id,
                object_id=payload.target_id,
                command_id=command_id,
                expected_version=payload.expected_version,
                expected_review_status=payload.expected_review_status,
                expected_lifecycle_status=payload.expected_lifecycle_status,
                rationale=human_reason,
                request_id=request_id,
            )
            method = _review_method(self._knowledge, payload.operation)
            event_id = (await method(command)).event_id
        elif isinstance(payload, EvidenceReviewPayload):
            evidence_command = EvidenceReviewCommand(
                project_id=proposal.project_id,
                knowledge_object_id=payload.knowledge_object_id,
                evidence_link_id=payload.target_id,
                command_id=command_id,
                expected_version=payload.expected_version,
                expected_review_status=payload.expected_review_status,
                expected_lifecycle_status=payload.expected_lifecycle_status,
                rationale=human_reason,
                request_id=request_id,
            )
            method = _evidence_review_method(self._knowledge, payload.operation)
            event_id = (await method(evidence_command)).event_id
        elif isinstance(payload, MethodElementRevisionPayload):
            event_id = (
                await self._methods.revise_element(
                    MethodElementRevisionCommand(
                        project_id=proposal.project_id,
                        method_model_id=payload.method_model_id,
                        element_id=payload.target_id,
                        command_id=command_id,
                        expected_version=payload.expected_version,
                        expected_review_status=payload.expected_review_status,
                        expected_lifecycle_status=payload.expected_lifecycle_status,
                        rationale=human_reason,
                        element_type=MethodElementType(payload.element_type),
                        epistemic_status=MethodEpistemicStatus(payload.epistemic_status),
                        confidence=payload.confidence,
                        subtype_payload=payload.subtype_payload.model_dump(
                            mode="json", exclude_none=False
                        ),
                        request_id=request_id,
                    )
                )
            ).event_id
        elif isinstance(payload, MethodElementReviewPayload):
            method = _method_element_review_method(self._methods, payload.operation)
            event_id = (
                await method(
                    MethodElementTransitionCommand(
                        project_id=proposal.project_id,
                        method_model_id=payload.method_model_id,
                        element_id=payload.target_id,
                        command_id=command_id,
                        expected_version=payload.expected_version,
                        expected_review_status=payload.expected_review_status,
                        expected_lifecycle_status=payload.expected_lifecycle_status,
                        rationale=human_reason,
                        request_id=request_id,
                    )
                )
            ).event_id
        elif isinstance(payload, MethodEvidenceReviewPayload):
            method = _method_evidence_review_method(self._methods, payload.operation)
            event_id = (
                await method(
                    MethodEvidenceReviewCommand(
                        project_id=proposal.project_id,
                        method_model_id=payload.method_model_id,
                        element_id=payload.element_id,
                        evidence_link_id=payload.target_id,
                        command_id=command_id,
                        expected_version=payload.expected_version,
                        expected_review_status=payload.expected_review_status,
                        expected_lifecycle_status=payload.expected_lifecycle_status,
                        rationale=human_reason,
                        request_id=request_id,
                    )
                )
            ).event_id
        elif isinstance(payload, MethodModelReviewPayload):
            if payload.operation != "accept":
                raise DomainValidationError(details={"reason": "copilot_review_operation_invalid"})
            event_id = (
                await self._methods.accept_model(
                    AcceptMethodModelCommand(
                        project_id=proposal.project_id,
                        method_model_id=payload.target_id,
                        command_id=command_id,
                        expected_version=payload.expected_version,
                        expected_review_status=payload.expected_review_status,
                        expected_lifecycle_status=payload.expected_lifecycle_status,
                        expected_composition_fingerprint=payload.expected_composition_fingerprint,
                        rationale=human_reason,
                        request_id=request_id,
                    )
                )
            ).event_id
        elif isinstance(payload, MappingRevisionPayload):
            event_id = (
                await self._mappings.create_semantic_revision(
                    MappingRevisionCommand(
                        project_id=proposal.project_id,
                        mapping_id=payload.target_id,
                        command_id=command_id,
                        expected_version=payload.expected_version,
                        expected_review_status=payload.expected_review_status,
                        expected_lifecycle_status=payload.expected_lifecycle_status,
                        rationale=human_reason,
                        paper_anchor_type=PaperAnchorType(payload.paper_anchor_type),
                        anchor_id=payload.anchor_id,
                        repository_component_id=payload.repository_component_id,
                        mapping_type=PaperCodeMappingType(payload.mapping_type),
                        explanation=payload.explanation,
                        confidence=payload.confidence,
                        request_id=request_id,
                    )
                )
            ).event_id
        elif isinstance(payload, MappingReviewPayload):
            method = _mapping_review_method(self._mappings, payload.operation)
            event_id = (
                await method(
                    MappingTransitionCommand(
                        project_id=proposal.project_id,
                        mapping_id=payload.target_id,
                        command_id=command_id,
                        expected_version=payload.expected_version,
                        expected_review_status=payload.expected_review_status,
                        expected_lifecycle_status=payload.expected_lifecycle_status,
                        rationale=human_reason,
                        request_id=request_id,
                    )
                )
            ).event_id
        elif isinstance(payload, BlueprintReviewPayload):
            if self._blueprints is None:
                raise ResourceConflictError(details={"reason": "blueprint_action_adapter_missing"})
            if payload.operation == "accept":
                event_id = (
                    await self._blueprints.accept(
                        AcceptBlueprintCommand(
                            project_id=proposal.project_id,
                            blueprint_id=payload.blueprint_id,
                            command_id=command_id,
                            expected_version=payload.expected_version,
                            expected_status=payload.expected_status,
                            rationale=human_reason,
                            request_id=request_id,
                        )
                    )
                ).event.id
            elif payload.operation == "request_revision":
                if self._blueprint_policy is None or payload.feedback is None:
                    raise ResourceConflictError(
                        details={"reason": "blueprint_revision_context_missing"}
                    )
                event_id = (
                    await self._blueprints.request_revision(
                        ReviseBlueprintCommand(
                            project_id=proposal.project_id,
                            blueprint_id=payload.blueprint_id,
                            command_id=command_id,
                            expected_version=payload.expected_version,
                            expected_status=payload.expected_status,
                            rationale=human_reason,
                            feedback=payload.feedback,
                            model_policy=self._blueprint_policy,
                            eligibility_assessment_id=payload.eligibility_assessment_id,
                            request_id=request_id,
                        )
                    )
                ).event.id
            else:
                raise DomainValidationError(details={"reason": "copilot_review_operation_invalid"})
        elif isinstance(payload, HumanJudgmentPayload):
            if self._judgments is None or rationale is None:
                raise ResourceConflictError(details={"reason": "human_judgment_executor_missing"})
            event_id = (
                await self._judgments.execute(
                    payload=payload,
                    project_id=proposal.project_id,
                    command_id=command_id,
                    rationale=rationale,
                    request_id=request_id,
                )
            ).id
        elif isinstance(payload, VerificationResultPayload):
            event_id = (
                await self._verification.record_result(
                    RecordVerificationResultCommand(
                        project_id=proposal.project_id,
                        check_id=payload.check_id,
                        command_id=command_id,
                        expected_result_status=payload.expected_result_status,
                        result_status=payload.result_status,
                        actual_result=payload.actual_result,
                        rationale=human_reason,
                        request_id=request_id,
                    )
                )
            ).event.id
        elif isinstance(payload, SpecializedIngestionProposalPayload):  # guarded before confirm
            raise ResourceConflictError(
                details={"reason": "specialized_ingestion_handoff_required"}
            )
        else:
            raise ResourceConflictError(details={"reason": "copilot_action_adapter_unavailable"})
        async with self._uow_factory(proposal.project_id) as uow:
            event = await uow.events.get_exact(event_id)
            await uow.rollback()
        if event is None:
            raise ResourceConflictError(details={"reason": "copilot_action_outcome_missing"})
        return event

    async def _find_outcome(
        self, project_id: UUID, proposal: CopilotActionProposalData
    ) -> OperationEventData | None:
        if proposal.outcome_event_id is not None:
            async with self._uow_factory(project_id) as uow:
                event = await uow.events.get_exact(proposal.outcome_event_id)
                await uow.rollback()
                return event
        command_id = uuid5(_NAMESPACE, f"execute:{proposal.id}")
        keys = _event_keys(CopilotActionType(proposal.action_type), command_id)
        async with self._uow_factory(project_id) as uow:
            for operation, key in keys:
                event = await uow.events.get_by_idempotency_key(operation, key)
                if event is not None:
                    await uow.rollback()
                    return event
            await uow.rollback()
        return None

    async def _mark_executed(
        self,
        project_id: UUID,
        proposal: CopilotActionProposalData,
        event: OperationEventData,
    ) -> CopilotActionProposalData:
        if proposal.status == CopilotProposalStatus.EXECUTED.value:
            if proposal.outcome_event_id != event.id:
                raise ResourceConflictError(details={"reason": "copilot_outcome_changed"})
            return proposal
        async with self._uow_factory(project_id) as uow:
            await self._owned(uow)
            updated = await uow.copilot.transition_proposal(
                proposal.id,
                expected_status=CopilotProposalStatus.CONFIRMED_PENDING.value,
                status=CopilotProposalStatus.EXECUTED.value,
                outcome_event_id=event.id,
            )
            await uow.commit()
            return updated

    async def _invalidate(
        self, project_id: UUID, proposal: CopilotActionProposalData, reason: str
    ) -> None:
        if proposal.status == CopilotProposalStatus.INVALIDATED.value:
            return
        async with self._uow_factory(project_id) as uow:
            await self._owned(uow)
            await uow.copilot.transition_proposal(
                proposal.id,
                expected_status=proposal.status,
                status=CopilotProposalStatus.INVALIDATED.value,
                invalidation_reason=reason,
            )
            await uow.commit()

    async def _load(
        self, project_id: UUID, proposal_id: UUID
    ) -> tuple[
        CopilotActionProposalData,
        CopilotProposedAction,
        object,
        tuple[CopilotManifestData, ...],
    ]:
        async with self._uow_factory(project_id) as uow:
            await self._owned(uow)
            proposal = await uow.copilot.get_proposal(proposal_id)
            if proposal is None:
                raise ResourceNotFoundError(details={"resource": "copilot_action_proposal"})
            manifest = await uow.copilot.get_manifest_by_fingerprint(
                proposal.context_manifest_fingerprint
            )
            if manifest is None:
                raise ResourceConflictError(details={"reason": "copilot_action_manifest_missing"})
            manifests: tuple[CopilotManifestData, ...] = (manifest,)
            if manifest.parent_manifest_id is not None:
                parent = await uow.copilot.get_manifest(manifest.parent_manifest_id)
                if parent is None:
                    raise ResourceConflictError(
                        details={"reason": "copilot_action_manifest_missing"}
                    )
                manifests = (parent, manifest)
            await uow.rollback()
        action = CopilotProposedAction.model_validate_json(
            json.dumps(
                {
                    "action_type": proposal.action_type,
                    "schema_version": proposal.schema_version,
                    "manifest_id": manifest.id,
                    "payload": proposal.payload,
                    "explanation": proposal.model_explanation,
                },
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ),
            strict=True,
        )
        if action.schema_version != COPILOT_ACTION_SCHEMA_VERSION:
            raise ResourceConflictError(details={"reason": "copilot_action_schema_changed"})
        return proposal, action, validate_action_payload(action), manifests

    async def _expected_state(
        self,
        project_id: UUID,
        manifests: tuple[CopilotManifestData, ...],
        referenced: set[UUID],
    ) -> dict[str, object]:
        selected = {
            identifier: item
            for manifest in manifests
            for item in manifest.ordered_items
            for identifier in _manifest_reference_ids((item,))
            if identifier in referenced
        }
        if set(selected) != referenced:
            raise DomainValidationError(details={"reason": "copilot_action_target_not_in_manifest"})
        async with self._uow_factory(project_id) as uow:
            await self._owned(uow)
            structured = {
                item.id: item for item in await uow.copilot.get_structured_items(tuple(referenced))
            }
            current: list[dict[str, object]] = []
            for identifier in sorted(referenced, key=str):
                manifest_item = selected[identifier]
                live = structured.get(identifier)
                if live is not None:
                    current.append(
                        {
                            "id": str(identifier),
                            "kind": live.kind,
                            "identity": live.identity,
                            "state": live.state,
                            "provenance": live.provenance,
                        }
                    )
                    continue
                kind = str(manifest_item.get("kind"))
                if kind == "source_chunk":
                    chunk = await uow.sources.get_chunk_exact(identifier)
                    if chunk is None:
                        raise ResourceConflictError(
                            details={"reason": "copilot_action_target_missing"}
                        )
                    current.append(
                        {"id": str(identifier), "kind": kind, "content_hash": chunk.content_hash}
                    )
                elif kind == "repository_component":
                    component = await uow.repositories.get_component_exact(identifier)
                    if component is None:
                        raise ResourceConflictError(
                            details={"reason": "copilot_action_target_missing"}
                        )
                    current.append(
                        {
                            "id": str(identifier),
                            "kind": kind,
                            "content_hash": component.content_hash,
                        }
                    )
                else:
                    evidence = await uow.knowledge.get_evidence_link_exact(identifier)
                    method_evidence = await uow.method_models.get_evidence_link_exact(identifier)
                    request = await uow.judgments.get_request_exact(identifier)
                    judgment = await uow.judgments.get_judgment_exact(identifier)
                    workflow = await uow.workflows.get_exact(identifier)
                    event = await uow.events.get_exact(identifier)
                    if evidence is not None:
                        current.append(
                            {
                                "id": str(identifier),
                                "kind": "evidence_link",
                                "version": evidence.version,
                                "review_status": evidence.review_status,
                                "lifecycle_status": evidence.lifecycle_status,
                            }
                        )
                    elif method_evidence is not None:
                        current.append(
                            {
                                "id": str(identifier),
                                "kind": "method_evidence_link",
                                "version": method_evidence.version,
                                "review_status": method_evidence.review_status,
                                "lifecycle_status": method_evidence.lifecycle_status,
                            }
                        )
                    elif request is not None:
                        current.append(
                            {
                                "id": str(identifier),
                                "kind": "judgment_request",
                                "version": request.version,
                                "status": request.status,
                                "lifecycle_status": request.lifecycle_status,
                            }
                        )
                    elif judgment is not None:
                        current.append(
                            {
                                "id": str(identifier),
                                "kind": "human_judgment",
                                "version": judgment.version,
                                "lifecycle_status": judgment.lifecycle_status,
                            }
                        )
                    elif workflow is not None:
                        current.append(
                            {
                                "id": str(identifier),
                                "kind": "workflow_run",
                                "status": workflow.status,
                                "current_stage": workflow.current_stage,
                                "lock_version": workflow.lock_version,
                            }
                        )
                    elif event is not None:
                        current.append(
                            {
                                "id": str(identifier),
                                "kind": "operation_event",
                                "operation_type": event.operation_type,
                                "input_fingerprint": event.input_fingerprint,
                            }
                        )
                    else:
                        current.append(
                            {
                                "id": str(identifier),
                                "kind": "manifest_embedded_reference",
                                "manifest_item_fingerprint": _fingerprint(manifest_item),
                            }
                        )
            await uow.rollback()
        return {
            "action_policy_version": COPILOT_ACTION_POLICY_VERSION,
            "manifest_id": str(manifests[-1].id),
            "manifest_fingerprint": manifests[-1].manifest_fingerprint,
            "manifest_chain": [
                {"id": str(item.id), "fingerprint": item.manifest_fingerprint} for item in manifests
            ],
            "targets": current,
        }

    async def _preview(
        self,
        project_id: UUID,
        action_type: CopilotActionType,
        payload: object,
        expected: dict[str, object],
    ) -> CopilotImpactPreview:
        confirmation_class = confirmation_class_for(action_type)
        declared = _declared_effect(action_type, payload)
        effects: tuple[dict[str, object], ...] = (declared,)
        if confirmation_class is ConfirmationClass.LIGHTWEIGHT:
            warning = "Candidate save has no accepted/current staleness effect."
            scope = "candidate_noncanonical"
            complete = True
        elif confirmation_class is ConfirmationClass.SPECIALIZED_HANDOFF:
            warning = (
                "This proposal cannot ingest a resource directly; the specialized ingestion "
                "boundary must validate and confirm it separately."
            )
            scope = "specialized_handoff"
            complete = True
        else:
            warning = (
                "This bounded preview includes the exact command target and direct persisted "
                "dependents only; additional downstream effects may appear during later normal "
                "propagation."
            )
            scope = "direct"
            complete = False
        operation = _staleness_operation(action_type, payload)
        if isinstance(payload, HumanJudgmentPayload):
            operation = await self._judgment_staleness_operation(project_id, payload)
        if operation is not None:
            cause_type, cause_id, operation_type = operation
            direct = await self._staleness.preview_direct(
                DirectStalenessCommand(
                    project_id=project_id,
                    cause_type=cause_type,
                    cause_id=cause_id,
                    operation_type=operation_type,
                )
            )
            dependent_effects: tuple[dict[str, object], ...] = tuple(
                {
                    "dependency_id": str(item.dependency_id),
                    "artifact_type": item.artifact_type,
                    "artifact_id": str(item.artifact_id),
                    "effect": item.effect,
                    "reason_code": item.reason_code,
                }
                for item in direct.effects
            )
            effects = (declared, *dependent_effects)
            warning, scope, complete = direct.warning, direct.scope, direct.complete
        identity = {
            "policy_version": COPILOT_PREVIEW_POLICY_VERSION,
            "action_policy_version": COPILOT_ACTION_POLICY_VERSION,
            "scope": scope,
            "complete": complete,
            "warning": warning,
            "expected_state": expected,
            "effects": effects,
        }
        return CopilotImpactPreview(
            COPILOT_PREVIEW_POLICY_VERSION,
            scope,
            complete,
            warning,
            expected,
            effects,
            _fingerprint(identity),
        )

    async def _judgment_staleness_operation(
        self, project_id: UUID, payload: HumanJudgmentPayload
    ) -> tuple[str, UUID, str]:
        async with self._uow_factory(project_id) as uow:
            await self._owned(uow)
            request = await uow.judgments.get_request_exact(payload.judgment_request_id)
            current = await uow.judgments.get_current_for_request(payload.judgment_request_id)
            await uow.rollback()
        if request is None:
            raise ResourceNotFoundError(details={"resource": "judgment_request"})
        if current is None:
            return (
                "knowledge_object",
                request.ambiguity_id,
                "human_judgment.ambiguity_resolved",
            )
        return "human_judgment", current.id, "human_judgment.revised"

    async def _confirmations(
        self, project_id: UUID, proposal_id: UUID
    ) -> tuple[CopilotActionConfirmationData, ...]:
        async with self._uow_factory(project_id) as uow:
            await self._owned(uow)
            values = await uow.copilot.list_confirmations(proposal_id)
            await uow.rollback()
            return values

    async def _owned(self, uow: UnitOfWork) -> None:
        project = await uow.projects.get()
        if project is None or project.created_by_id != self._actor.actor_id:
            raise ResourceNotFoundError(details={"resource": "project"})


def _payload_ids(value: object) -> set[UUID]:
    identifiers: set[UUID] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key.endswith("_id") and item is not None:
                try:
                    identifiers.add(UUID(str(item)))
                except ValueError as error:
                    raise DomainValidationError(
                        details={"reason": "copilot_action_id_invalid"}
                    ) from error
            else:
                identifiers.update(_payload_ids(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            identifiers.update(_payload_ids(item))
    return identifiers


def _manifest_reference_ids(value: object) -> set[UUID]:
    identifiers: set[UUID] = set()
    if isinstance(value, dict):
        for item in value.values():
            identifiers.update(_manifest_reference_ids(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            identifiers.update(_manifest_reference_ids(item))
    elif isinstance(value, str):
        try:
            identifiers.add(UUID(value))
        except ValueError:
            pass
    return identifiers


def _declared_effect(action_type: CopilotActionType, payload: object) -> dict[str, object]:
    if isinstance(payload, KnowledgeCandidatePayload):
        return {"artifact_type": "knowledge_object", "effect": "candidate_created"}
    if isinstance(payload, EvidenceCandidatePayload):
        return {
            "artifact_type": "evidence_link",
            "artifact_id": str(payload.knowledge_object_id),
            "effect": "candidate_created",
        }
    if isinstance(payload, HumanJudgmentPayload):
        return {
            "artifact_type": "human_judgment",
            "artifact_id": str(payload.judgment_request_id),
            "effect": "human_decision_recorded",
        }
    if isinstance(payload, VerificationResultPayload):
        return {
            "artifact_type": "verification_check",
            "artifact_id": str(payload.check_id),
            "effect": "result_recorded",
        }
    if isinstance(payload, BlueprintReviewPayload):
        return {
            "artifact_type": "implementation_blueprint",
            "artifact_id": str(payload.blueprint_id),
            "effect": payload.operation,
        }
    target = getattr(payload, "target_id", None)
    return {
        "artifact_type": action_type.value.split(".", maxsplit=1)[0],
        "artifact_id": None if target is None else str(target),
        "effect": action_type.value,
    }


def _review_method(service: KnowledgeReviewService, operation: str):  # type: ignore[no-untyped-def]
    methods = {
        "accept": service.accept,
        "reject": service.reject,
        "request_revision": service.request_revision,
        "challenge": service.challenge,
        "reaffirm": service.reaffirm,
    }
    try:
        return methods[operation]
    except KeyError as error:
        raise DomainValidationError(
            details={"reason": "copilot_review_operation_invalid"}
        ) from error


def _evidence_review_method(service: KnowledgeReviewService, operation: str):  # type: ignore[no-untyped-def]
    methods = {
        "accept": service.accept_evidence,
        "reject": service.reject_evidence,
        "request_revision": service.request_evidence_revision,
        "challenge": service.challenge_evidence,
        "reaffirm": service.reaffirm_evidence,
    }
    try:
        return methods[operation]
    except KeyError as error:
        raise DomainValidationError(
            details={"reason": "copilot_review_operation_invalid"}
        ) from error


def _method_element_review_method(service: MethodReviewService, operation: str):  # type: ignore[no-untyped-def]
    methods = {
        "accept": service.accept_element,
        "reject": service.reject_element,
        "request_revision": service.request_element_revision,
        "challenge": service.challenge_element,
        "reaffirm": service.reaffirm_element,
    }
    try:
        return methods[operation]
    except KeyError as error:
        raise DomainValidationError(
            details={"reason": "copilot_review_operation_invalid"}
        ) from error


def _method_evidence_review_method(service: MethodReviewService, operation: str):  # type: ignore[no-untyped-def]
    methods = {
        "accept": service.accept_evidence,
        "reject": service.reject_evidence,
        "request_revision": service.request_evidence_revision,
        "challenge": service.challenge_evidence,
        "reaffirm": service.reaffirm_evidence,
    }
    try:
        return methods[operation]
    except KeyError as error:
        raise DomainValidationError(
            details={"reason": "copilot_review_operation_invalid"}
        ) from error


def _mapping_review_method(service: MappingReviewService, operation: str):  # type: ignore[no-untyped-def]
    methods = {
        "accept": service.accept,
        "reject": service.reject,
        "request_revision": service.request_revision,
        "challenge": service.challenge,
        "reaffirm": service.reaffirm,
    }
    try:
        return methods[operation]
    except KeyError as error:
        raise DomainValidationError(
            details={"reason": "copilot_review_operation_invalid"}
        ) from error


def _staleness_operation(
    action_type: CopilotActionType, payload: object
) -> tuple[str, UUID, str] | None:
    if action_type is CopilotActionType.REVIEW_KNOWLEDGE_OBJECT and isinstance(
        payload, ReviewTargetPayload
    ):
        return "knowledge_object", payload.target_id, f"knowledge_object.{payload.operation}"
    if action_type is CopilotActionType.REVIEW_EVIDENCE_LINK and isinstance(
        payload, EvidenceReviewPayload
    ):
        return "evidence_link", payload.target_id, f"evidence_link.{payload.operation}"
    if action_type is CopilotActionType.REVIEW_METHOD_ELEMENT and isinstance(
        payload, MethodElementReviewPayload
    ):
        return "method_element", payload.target_id, f"method_element.{payload.operation}"
    if action_type is CopilotActionType.REVIEW_MAPPING and isinstance(
        payload, MappingReviewPayload
    ):
        return "paper_code_mapping", payload.target_id, f"paper_code_mapping.{payload.operation}"
    return None


def _event_keys(action_type: CopilotActionType, command_id: UUID) -> tuple[tuple[str, str], ...]:
    if action_type is CopilotActionType.CREATE_KNOWLEDGE_CANDIDATE:
        return (
            ("copilot.knowledge_candidate.created", f"copilot-knowledge-candidate:{command_id}"),
        )
    if action_type is CopilotActionType.CREATE_EVIDENCE_CANDIDATE:
        return (("copilot.evidence_candidate.created", f"copilot-evidence-candidate:{command_id}"),)
    if action_type is CopilotActionType.RECORD_VERIFICATION_RESULT:
        return (("verification_check.result_recorded", str(command_id)),)
    if action_type is CopilotActionType.RESOLVE_HUMAN_JUDGMENT:
        return (("human_judgment.recorded", f"human-judgment-command:{command_id}"),)
    fixed = {
        CopilotActionType.REVISE_KNOWLEDGE_OBJECT: "knowledge_object.semantic_revision",
        CopilotActionType.REVISE_METHOD_ELEMENT: "method_element.semantic_revision",
        CopilotActionType.REVIEW_METHOD_MODEL: "method_model.accept",
        CopilotActionType.REVISE_MAPPING: "paper_code_mapping.semantic_revision",
    }.get(action_type)
    if fixed is not None:
        return ((fixed, str(command_id)),)
    prefixes = {
        CopilotActionType.REVIEW_KNOWLEDGE_OBJECT: "knowledge_object",
        CopilotActionType.REVIEW_EVIDENCE_LINK: "evidence_link",
        CopilotActionType.REVIEW_METHOD_ELEMENT: "method_element",
        CopilotActionType.REVIEW_METHOD_EVIDENCE: "method_evidence",
        CopilotActionType.REVIEW_MAPPING: "paper_code_mapping",
    }
    prefix = prefixes.get(action_type)
    if prefix is not None:
        return tuple(
            (f"{prefix}.{operation}", str(command_id))
            for operation in ("accept", "reject", "request_revision", "challenge", "reaffirm")
        )
    if action_type is CopilotActionType.REVIEW_BLUEPRINT:
        return (
            ("blueprint.accepted", str(command_id)),
            ("blueprint.revision_requested", str(command_id)),
        )
    return ()


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
