"""SQLAlchemy persistence for noncanonical Copilot audit state."""

from datetime import UTC, datetime
from typing import Any, TypeVar, cast
from uuid import UUID

from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from researchflow.errors import ResourceConflictError
from researchflow.infrastructure.database.models.blueprint import (
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
from researchflow.infrastructure.database.models.dependency import ArtifactDependencyRecord
from researchflow.infrastructure.database.models.evidence import EvidenceLinkRecord
from researchflow.infrastructure.database.models.judgment import (
    HumanJudgmentRecord,
    JudgmentRequestRecord,
)
from researchflow.infrastructure.database.models.knowledge import KnowledgeObjectRecord
from researchflow.infrastructure.database.models.mapping import PaperCodeMappingRecord
from researchflow.infrastructure.database.models.mental_model import (
    MentalModelMemberRecord,
    MentalModelRecord,
)
from researchflow.infrastructure.database.models.method_element import MethodElementRecord
from researchflow.infrastructure.database.models.method_model import (
    MethodModelMemberRecord,
    MethodModelRecord,
)
from researchflow.infrastructure.database.models.repository import (
    PaperRepositoryRelationshipRecord,
    RepositoryComponentRecord,
    RepositoryInspectionRunRecord,
    RepositoryRecord,
    RepositorySnapshotRecord,
)
from researchflow.infrastructure.database.models.source import (
    SourceChunkRecord,
    SourceProcessingRunRecord,
    SourceRecord,
)
from researchflow.infrastructure.database.models.workflow import WorkflowRunRecord
from researchflow.infrastructure.database.repositories._errors import execute_database
from researchflow.ports.copilot import (
    CopilotActionConfirmationData,
    CopilotActionProposalData,
    CopilotEvidenceItemData,
    CopilotManifestData,
    CopilotMessageData,
    CopilotStructuredItemData,
    CopilotThreadData,
    CopilotTurnData,
    NewCopilotActionConfirmation,
    NewCopilotActionProposal,
    NewCopilotManifest,
    NewCopilotMessage,
    NewCopilotThread,
    NewCopilotTurn,
)

_RecordT = TypeVar(
    "_RecordT",
    CopilotThreadRecord,
    CopilotMessageRecord,
    CopilotTurnRecord,
    CopilotActionProposalRecord,
    CopilotActionConfirmationRecord,
    CopilotManifestRecord,
)
_RowT = TypeVar("_RowT")


def _thread(row: CopilotThreadRecord) -> CopilotThreadData:
    return CopilotThreadData(
        **{field: getattr(row, field) for field in CopilotThreadData.__dataclass_fields__}
    )


def _message(row: CopilotMessageRecord) -> CopilotMessageData:
    return CopilotMessageData(
        **{field: getattr(row, field) for field in CopilotMessageData.__dataclass_fields__}
    )


def _turn(row: CopilotTurnRecord) -> CopilotTurnData:
    return CopilotTurnData(
        **{field: getattr(row, field) for field in CopilotTurnData.__dataclass_fields__}
    )


def _proposal(row: CopilotActionProposalRecord) -> CopilotActionProposalData:
    values = {
        field: getattr(row, field) for field in CopilotActionProposalData.__dataclass_fields__
    }
    values["payload"] = dict(row.payload)
    values["expected_state"] = dict(row.expected_state)
    return CopilotActionProposalData(**values)


def _confirmation(row: CopilotActionConfirmationRecord) -> CopilotActionConfirmationData:
    return CopilotActionConfirmationData(
        **{
            field: getattr(row, field)
            for field in CopilotActionConfirmationData.__dataclass_fields__
        }
    )


def _manifest(row: CopilotManifestRecord) -> CopilotManifestData:
    return CopilotManifestData(
        id=row.id,
        project_id=row.project_id,
        kind=row.kind,
        parent_manifest_id=row.parent_manifest_id,
        selector=dict(row.selector),
        selector_fingerprint=row.selector_fingerprint,
        state_fingerprint=row.state_fingerprint,
        policy_version=row.policy_version,
        bounds_policy_version=row.bounds_policy_version,
        ordered_items=tuple(dict(item) for item in row.ordered_items),
        limitations=tuple(dict(item) for item in row.limitations),
        truncated=row.truncated,
        manifest_fingerprint=row.manifest_fingerprint,
        created_at=row.created_at,
    )


def _text_hash(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode()).hexdigest()


class SqlAlchemyCopilotRepository:
    def __init__(self, session: AsyncSession, project_id: UUID) -> None:
        self._session = session
        self.project_id = project_id

    def _scope(self, project_id: UUID) -> None:
        if project_id != self.project_id:
            raise ResourceConflictError(details={"reason": "project_scope_mismatch"})

    async def add_thread(self, value: NewCopilotThread) -> CopilotThreadData:
        self._scope(value.project_id)
        row = CopilotThreadRecord(
            **value.__dict__
            if hasattr(value, "__dict__")
            else {field: getattr(value, field) for field in value.__dataclass_fields__}
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return _thread(row)

    async def get_thread(self, thread_id: UUID) -> CopilotThreadData | None:
        row = await self._one(CopilotThreadRecord, thread_id)
        return None if row is None else _thread(row)

    async def get_thread_for_update(self, thread_id: UUID) -> CopilotThreadData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(CopilotThreadRecord)
                .where(
                    CopilotThreadRecord.id == thread_id,
                    CopilotThreadRecord.project_id == self.project_id,
                )
                .with_for_update()
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _thread(row)

    async def list_threads(self) -> tuple[CopilotThreadData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(CopilotThreadRecord)
                .where(CopilotThreadRecord.project_id == self.project_id)
                .order_by(CopilotThreadRecord.updated_at.desc(), CopilotThreadRecord.id)
            )
        )
        return tuple(_thread(row) for row in result.scalars())

    async def archive_thread(
        self, thread_id: UUID, *, expected_lock_version: int
    ) -> CopilotThreadData:
        now = datetime.now(UTC)
        result = await execute_database(
            lambda: self._session.execute(
                update(CopilotThreadRecord)
                .where(
                    CopilotThreadRecord.id == thread_id,
                    CopilotThreadRecord.project_id == self.project_id,
                    CopilotThreadRecord.lock_version == expected_lock_version,
                    CopilotThreadRecord.status == "active",
                )
                .values(
                    status="archived",
                    archived_at=now,
                    updated_at=now,
                    lock_version=CopilotThreadRecord.lock_version + 1,
                )
                .returning(CopilotThreadRecord)
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise ResourceConflictError(details={"reason": "copilot_thread_state_changed"})
        return _thread(row)

    async def add_message(self, value: NewCopilotMessage) -> CopilotMessageData:
        self._scope(value.project_id)
        row = CopilotMessageRecord(
            **{field: getattr(value, field) for field in value.__dataclass_fields__}
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return _message(row)

    async def get_message(self, message_id: UUID) -> CopilotMessageData | None:
        row = await self._one(CopilotMessageRecord, message_id)
        return None if row is None else _message(row)

    async def list_messages(self, thread_id: UUID) -> tuple[CopilotMessageData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(CopilotMessageRecord)
                .where(
                    CopilotMessageRecord.project_id == self.project_id,
                    CopilotMessageRecord.thread_id == thread_id,
                )
                .order_by(CopilotMessageRecord.ordinal)
            )
        )
        return tuple(_message(row) for row in result.scalars())

    async def add_turn(self, value: NewCopilotTurn) -> CopilotTurnData:
        self._scope(value.project_id)
        row = CopilotTurnRecord(
            **{field: getattr(value, field) for field in value.__dataclass_fields__}
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return _turn(row)

    async def get_turn(self, turn_id: UUID) -> CopilotTurnData | None:
        row = await self._one(CopilotTurnRecord, turn_id)
        return None if row is None else _turn(row)

    async def get_turn_for_update(self, turn_id: UUID) -> CopilotTurnData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(CopilotTurnRecord)
                .where(
                    CopilotTurnRecord.id == turn_id, CopilotTurnRecord.project_id == self.project_id
                )
                .with_for_update()
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _turn(row)

    async def list_turns(self, thread_id: UUID) -> tuple[CopilotTurnData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(CopilotTurnRecord)
                .where(
                    CopilotTurnRecord.project_id == self.project_id,
                    CopilotTurnRecord.thread_id == thread_id,
                )
                .order_by(CopilotTurnRecord.started_at, CopilotTurnRecord.id)
            )
        )
        return tuple(_turn(row) for row in result.scalars())

    async def bind_retrieval_manifest(
        self,
        turn_id: UUID,
        *,
        expected_status: str,
        retrieval_manifest_fingerprint: str,
    ) -> CopilotTurnData:
        result = await execute_database(
            lambda: self._session.execute(
                update(CopilotTurnRecord)
                .where(
                    CopilotTurnRecord.id == turn_id,
                    CopilotTurnRecord.project_id == self.project_id,
                    CopilotTurnRecord.status == expected_status,
                    or_(
                        CopilotTurnRecord.retrieval_manifest_fingerprint.is_(None),
                        CopilotTurnRecord.retrieval_manifest_fingerprint
                        == retrieval_manifest_fingerprint,
                    ),
                )
                .values(retrieval_manifest_fingerprint=retrieval_manifest_fingerprint)
                .returning(CopilotTurnRecord)
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise ResourceConflictError(details={"reason": "copilot_turn_state_changed"})
        return _turn(row)

    async def finalize_turn(
        self,
        turn_id: UUID,
        *,
        expected_status: str,
        status: str,
        assistant_message_id: UUID | None,
        error_code: str | None,
    ) -> CopilotTurnData:
        result = await execute_database(
            lambda: self._session.execute(
                update(CopilotTurnRecord)
                .where(
                    CopilotTurnRecord.id == turn_id,
                    CopilotTurnRecord.project_id == self.project_id,
                    CopilotTurnRecord.status == expected_status,
                )
                .values(
                    status=status,
                    assistant_message_id=assistant_message_id,
                    error_code=error_code,
                    completed_at=func.now(),
                )
                .returning(CopilotTurnRecord)
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise ResourceConflictError(details={"reason": "copilot_turn_state_changed"})
        return _turn(row)

    async def add_proposal(self, value: NewCopilotActionProposal) -> CopilotActionProposalData:
        self._scope(value.project_id)
        row = CopilotActionProposalRecord(
            **{field: getattr(value, field) for field in value.__dataclass_fields__}
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return _proposal(row)

    async def get_proposal(self, proposal_id: UUID) -> CopilotActionProposalData | None:
        row = await self._one(CopilotActionProposalRecord, proposal_id)
        return None if row is None else _proposal(row)

    async def get_proposal_for_update(self, proposal_id: UUID) -> CopilotActionProposalData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(CopilotActionProposalRecord)
                .where(
                    CopilotActionProposalRecord.project_id == self.project_id,
                    CopilotActionProposalRecord.id == proposal_id,
                )
                .with_for_update()
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _proposal(row)

    async def list_proposals(self, turn_id: UUID) -> tuple[CopilotActionProposalData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(CopilotActionProposalRecord)
                .where(
                    CopilotActionProposalRecord.project_id == self.project_id,
                    CopilotActionProposalRecord.turn_id == turn_id,
                )
                .order_by(CopilotActionProposalRecord.created_at, CopilotActionProposalRecord.id)
            )
        )
        return tuple(_proposal(row) for row in result.scalars())

    async def transition_proposal(
        self,
        proposal_id: UUID,
        *,
        expected_status: str,
        status: str,
        outcome_event_id: UUID | None = None,
        invalidation_reason: str | None = None,
    ) -> CopilotActionProposalData:
        result = await execute_database(
            lambda: self._session.execute(
                update(CopilotActionProposalRecord)
                .where(
                    CopilotActionProposalRecord.project_id == self.project_id,
                    CopilotActionProposalRecord.id == proposal_id,
                    CopilotActionProposalRecord.status == expected_status,
                )
                .values(
                    status=status,
                    outcome_event_id=outcome_event_id,
                    invalidation_reason=invalidation_reason,
                )
                .returning(CopilotActionProposalRecord)
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise ResourceConflictError(details={"reason": "copilot_proposal_state_changed"})
        return _proposal(row)

    async def add_confirmation(
        self, value: NewCopilotActionConfirmation
    ) -> CopilotActionConfirmationData:
        self._scope(value.project_id)
        row = CopilotActionConfirmationRecord(
            **{field: getattr(value, field) for field in value.__dataclass_fields__}
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return _confirmation(row)

    async def list_confirmations(
        self, proposal_id: UUID
    ) -> tuple[CopilotActionConfirmationData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(CopilotActionConfirmationRecord)
                .where(
                    CopilotActionConfirmationRecord.project_id == self.project_id,
                    CopilotActionConfirmationRecord.proposal_id == proposal_id,
                )
                .order_by(
                    CopilotActionConfirmationRecord.created_at, CopilotActionConfirmationRecord.id
                )
            )
        )
        return tuple(_confirmation(row) for row in result.scalars())

    async def add_manifest(self, value: NewCopilotManifest) -> CopilotManifestData:
        self._scope(value.project_id)
        row = CopilotManifestRecord(
            id=value.id,
            project_id=value.project_id,
            kind=value.kind,
            parent_manifest_id=value.parent_manifest_id,
            selector=value.selector,
            selector_fingerprint=value.selector_fingerprint,
            state_fingerprint=value.state_fingerprint,
            policy_version=value.policy_version,
            bounds_policy_version=value.bounds_policy_version,
            ordered_items=list(value.ordered_items),
            limitations=list(value.limitations),
            truncated=value.truncated,
            manifest_fingerprint=value.manifest_fingerprint,
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return _manifest(row)

    async def get_manifest(self, manifest_id: UUID) -> CopilotManifestData | None:
        row = await self._one(CopilotManifestRecord, manifest_id)
        return None if row is None else _manifest(row)

    async def get_manifest_by_fingerprint(
        self, manifest_fingerprint: str
    ) -> CopilotManifestData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(CopilotManifestRecord).where(
                    CopilotManifestRecord.project_id == self.project_id,
                    CopilotManifestRecord.manifest_fingerprint == manifest_fingerprint,
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _manifest(row)

    async def list_context_items(
        self, research_question_id: UUID
    ) -> tuple[CopilotStructuredItemData, ...]:
        items: list[CopilotStructuredItemData] = []
        knowledge = await self._rows(
            select(KnowledgeObjectRecord).where(
                KnowledgeObjectRecord.project_id == self.project_id,
                KnowledgeObjectRecord.lifecycle_status != "superseded",
            )
        )
        for knowledge_row in knowledge:
            items.append(self._knowledge_item(knowledge_row))

        methods = await self._rows(
            select(MethodModelRecord).where(
                MethodModelRecord.project_id == self.project_id,
                MethodModelRecord.research_question_id == research_question_id,
                MethodModelRecord.lifecycle_status != "superseded",
            )
        )
        for method_row in methods:
            items.append(
                self._item(
                    "method_model",
                    method_row,
                    f"{method_row.name}: {method_row.objective}",
                    identity={
                        "lineage_id": str(method_row.lineage_id),
                        "version": method_row.version,
                        "composition_fingerprint": method_row.composition_fingerprint,
                    },
                    state={
                        "review_status": method_row.review_status,
                        "lifecycle_status": method_row.lifecycle_status,
                        "freshness_status": method_row.freshness_status,
                        "accepted_current": method_row.review_status == "accepted"
                        and method_row.lifecycle_status == "current"
                        and method_row.freshness_status == "current",
                    },
                    material=method_row.review_status == "accepted",
                )
            )

        mental_models = await self._rows(
            select(MentalModelRecord).where(
                MentalModelRecord.project_id == self.project_id,
                MentalModelRecord.status != "superseded",
            )
        )
        for mental_row in mental_models:
            items.append(
                self._item(
                    "mental_model",
                    mental_row,
                    mental_row.title,
                    identity={
                        "lineage_id": str(mental_row.lineage_id),
                        "version": mental_row.version,
                        "composition_fingerprint": mental_row.composition_fingerprint,
                    },
                    state={
                        "status": mental_row.status,
                        "accepted_current": mental_row.status == "current",
                    },
                    material=mental_row.status == "current",
                )
            )

        mappings = await self._rows(
            select(PaperCodeMappingRecord).where(
                PaperCodeMappingRecord.project_id == self.project_id,
                PaperCodeMappingRecord.lifecycle_status != "superseded",
            )
        )
        for mapping_row in mappings:
            items.append(
                self._item(
                    "paper_code_mapping",
                    mapping_row,
                    mapping_row.explanation,
                    identity={
                        "lineage_id": str(mapping_row.lineage_id),
                        "version": mapping_row.version,
                    },
                    state={
                        "review_status": mapping_row.review_status,
                        "lifecycle_status": mapping_row.lifecycle_status,
                        "freshness_status": mapping_row.freshness_status,
                        "mapping_type": mapping_row.mapping_type,
                        "accepted_current": mapping_row.review_status == "accepted"
                        and mapping_row.lifecycle_status == "current"
                        and mapping_row.freshness_status == "current",
                    },
                    provenance={
                        "inspection_run_id": str(mapping_row.repository_inspection_run_id),
                        "repository_component_id": None
                        if mapping_row.repository_component_id is None
                        else str(mapping_row.repository_component_id),
                        "absence_scope": "inspection_bounded"
                        if mapping_row.mapping_type == "absent_from_repo"
                        else None,
                    },
                    material=mapping_row.review_status == "accepted",
                )
            )

        requests = await self._rows(
            select(JudgmentRequestRecord).where(
                JudgmentRequestRecord.project_id == self.project_id,
                JudgmentRequestRecord.lifecycle_status == "current",
            )
        )
        for request_row in requests:
            items.append(
                self._item(
                    "judgment_request",
                    request_row,
                    f"{request_row.title}: {request_row.decision_question}",
                    identity={
                        "lineage_id": str(request_row.lineage_id),
                        "version": request_row.version,
                    },
                    state={
                        "status": request_row.status,
                        "lifecycle_status": request_row.lifecycle_status,
                    },
                    material=request_row.status == "open",
                )
            )
        judgments = await self._rows(
            select(HumanJudgmentRecord).where(
                HumanJudgmentRecord.project_id == self.project_id,
                HumanJudgmentRecord.lifecycle_status == "current",
            )
        )
        for judgment_row in judgments:
            content = judgment_row.custom_decision or (
                f"Selected option {judgment_row.selected_option_id}"
            )
            items.append(
                self._item(
                    "human_judgment",
                    judgment_row,
                    f"{content}. Human rationale: {judgment_row.rationale}",
                    identity={
                        "lineage_id": str(judgment_row.lineage_id),
                        "version": judgment_row.version,
                    },
                    state={
                        "lifecycle_status": judgment_row.lifecycle_status,
                        "authority": "human_decided",
                        "accepted_current": True,
                    },
                    material=True,
                )
            )

        blueprints = await self._rows(
            select(ImplementationBlueprintRecord).where(
                ImplementationBlueprintRecord.project_id == self.project_id,
                ImplementationBlueprintRecord.status != "superseded",
            )
        )
        for blueprint_row in blueprints:
            items.append(
                self._item(
                    "implementation_blueprint",
                    blueprint_row,
                    f"Blueprint version {blueprint_row.version}",
                    identity={
                        "lineage_id": str(blueprint_row.lineage_id),
                        "version": blueprint_row.version,
                    },
                    state={
                        "status": blueprint_row.status,
                        "eligibility_status": blueprint_row.eligibility_status,
                        "accepted_current": blueprint_row.status == "accepted",
                    },
                    material=blueprint_row.status == "accepted",
                )
            )
        workflows = await self._rows(
            select(WorkflowRunRecord).where(WorkflowRunRecord.project_id == self.project_id)
        )
        for workflow_row in workflows:
            items.append(
                self._item(
                    "workflow_run",
                    workflow_row,
                    f"{workflow_row.workflow_type}: {workflow_row.current_stage or 'not started'}",
                    state={
                        "status": workflow_row.status,
                        "current_stage": workflow_row.current_stage,
                    },
                )
            )

        relationships = await self._rows(
            select(PaperRepositoryRelationshipRecord).where(
                PaperRepositoryRelationshipRecord.project_id == self.project_id,
                PaperRepositoryRelationshipRecord.lifecycle_status == "current",
            )
        )
        for relationship_row in relationships:
            inspection = await self._rows(
                select(RepositoryInspectionRunRecord)
                .where(
                    RepositoryInspectionRunRecord.repository_snapshot_id
                    == relationship_row.repository_snapshot_id
                )
                .order_by(RepositoryInspectionRunRecord.created_at.desc())
                .limit(1)
            )
            latest = inspection[0] if inspection else None
            items.append(
                self._item(
                    "repository_relationship",
                    relationship_row,
                    relationship_row.rationale,
                    identity={
                        "lineage_id": str(relationship_row.lineage_id),
                        "version": relationship_row.version,
                    },
                    state={
                        "review_status": relationship_row.review_status,
                        "lifecycle_status": relationship_row.lifecycle_status,
                        "relationship_type": relationship_row.relationship_type,
                        "authority_level": relationship_row.authority_level,
                        "accepted_current": relationship_row.review_status == "accepted",
                    },
                    provenance={
                        "repository_snapshot_id": str(relationship_row.repository_snapshot_id),
                        "inspection_run_id": None if latest is None else str(latest.id),
                        "inspection_coverage": None if latest is None else latest.coverage_status,
                        "inspection_limits": None if latest is None else latest.configured_limits,
                    },
                    material=relationship_row.review_status == "accepted",
                )
            )
        return tuple(sorted(items, key=lambda item: (item.kind, str(item.id))))

    async def get_structured_items(
        self, target_ids: tuple[UUID, ...]
    ) -> tuple[CopilotStructuredItemData, ...]:
        if not target_ids:
            return ()
        items = await self.list_context_items(await self._primary_question_id())
        selected = {value for value in target_ids}
        found = [item for item in items if item.id in selected]
        method_model_ids = tuple(
            item.id for item in found if item.kind == "method_model" and item.id in selected
        )
        if method_model_ids:
            member_rows = await self._rows(
                select(MethodModelMemberRecord).where(
                    MethodModelMemberRecord.method_model_id.in_(method_model_ids)
                )
            )
            element_ids = tuple(member.method_element_id for member in member_rows)
            if element_ids:
                element_rows = await self._rows(
                    select(MethodElementRecord).where(
                        MethodElementRecord.project_id == self.project_id,
                        MethodElementRecord.id.in_(element_ids),
                    )
                )
                member_by_element = {member.method_element_id: member for member in member_rows}
                for element_row in element_rows:
                    member = member_by_element[element_row.id]
                    found.append(
                        self._method_element_item(
                            element_row,
                            provenance={
                                "method_model_id": str(member.method_model_id),
                                "member_id": str(member.id),
                                "review_section": member.review_section,
                                "member_role": member.member_role,
                                "sequence_index": member.sequence_index,
                            },
                        )
                    )

        mental_model_ids = tuple(
            item.id for item in found if item.kind == "mental_model" and item.id in selected
        )
        if mental_model_ids:
            mental_members = await self._rows(
                select(MentalModelMemberRecord).where(
                    MentalModelMemberRecord.mental_model_id.in_(mental_model_ids)
                )
            )
            member_targets = {
                target
                for member in mental_members
                for target in (member.knowledge_object_id, member.human_judgment_id)
                if target is not None
            }
            found.extend(item for item in items if item.id in member_targets)

        blueprint_ids = tuple(
            item.id
            for item in found
            if item.kind == "implementation_blueprint" and item.id in selected
        )
        if blueprint_ids:
            sections = await self._rows(
                select(BlueprintSectionRecord).where(
                    BlueprintSectionRecord.project_id == self.project_id,
                    BlueprintSectionRecord.blueprint_id.in_(blueprint_ids),
                )
            )
            for section_row in sections:
                found.append(
                    self._item(
                        "blueprint_section",
                        section_row,
                        str(section_row.structured_content),
                        identity={"sequence_index": section_row.sequence_index},
                        state={
                            "review_status": section_row.review_status,
                            "explicitness": section_row.explicitness,
                        },
                        provenance={"blueprint_id": str(section_row.blueprint_id)},
                    )
                )
            blueprint_steps = await self._rows(
                select(ImplementationStepRecord).where(
                    ImplementationStepRecord.project_id == self.project_id,
                    ImplementationStepRecord.blueprint_id.in_(blueprint_ids),
                    ImplementationStepRecord.lifecycle_status != "superseded",
                )
            )
            for step_row in blueprint_steps:
                found.append(self._step_item(step_row))
            checks = await self._rows(
                select(VerificationCheckRecord).where(
                    VerificationCheckRecord.project_id == self.project_id,
                    VerificationCheckRecord.blueprint_id.in_(blueprint_ids),
                    VerificationCheckRecord.lifecycle_status != "superseded",
                )
            )
            for check_row in checks:
                found.append(self._check_item(check_row))

        remaining = selected - {item.id for item in found}
        if remaining:
            elements = await self._rows(
                select(MethodElementRecord).where(
                    MethodElementRecord.project_id == self.project_id,
                    MethodElementRecord.id.in_(remaining),
                )
            )
            for element_row in elements:
                found.append(self._method_element_item(element_row))
            sources = await self._rows(
                select(SourceRecord).where(
                    SourceRecord.project_id == self.project_id, SourceRecord.id.in_(remaining)
                )
            )
            for source_row in sources:
                found.append(
                    self._item(
                        "source",
                        source_row,
                        source_row.title,
                        identity={"content_hash": source_row.content_hash},
                        state={
                            "processing_status": source_row.processing_status,
                            "accepted_current": False,
                            "evidence_container": True,
                        },
                    )
                )
            steps = await self._rows(
                select(ImplementationStepRecord).where(
                    ImplementationStepRecord.project_id == self.project_id,
                    ImplementationStepRecord.id.in_(remaining),
                )
            )
            for step_row in steps:
                found.append(self._step_item(step_row))
            checks = await self._rows(
                select(VerificationCheckRecord).where(
                    VerificationCheckRecord.project_id == self.project_id,
                    VerificationCheckRecord.id.in_(remaining),
                )
            )
            for check_row in checks:
                found.append(self._check_item(check_row))
        unique = {(item.kind, item.id): item for item in found}
        return tuple(sorted(unique.values(), key=lambda item: (item.kind, str(item.id))))

    async def search_structured_items(
        self, *, query: str, allowed_kinds: tuple[str, ...], limit: int
    ) -> tuple[CopilotStructuredItemData, ...]:
        # PostgreSQL FTS determines matches; stable application ordering is retained.
        tsquery = func.websearch_to_tsquery("simple", query)
        matched_ids: set[UUID] = set()
        if not allowed_kinds or "knowledge_object" in allowed_kinds:
            matched_ids.update(
                row.id
                for row in await self._rows(
                    select(KnowledgeObjectRecord).where(
                        KnowledgeObjectRecord.project_id == self.project_id,
                        func.to_tsvector("simple", KnowledgeObjectRecord.statement).op("@@")(
                            tsquery
                        ),
                    )
                )
            )
        if not allowed_kinds or "method_model" in allowed_kinds:
            matched_ids.update(
                row.id
                for row in await self._rows(
                    select(MethodModelRecord).where(
                        MethodModelRecord.project_id == self.project_id,
                        func.to_tsvector(
                            "simple", MethodModelRecord.name + " " + MethodModelRecord.objective
                        ).op("@@")(tsquery),
                    )
                )
            )
        if not allowed_kinds or "judgment_request" in allowed_kinds:
            matched_ids.update(
                row.id
                for row in await self._rows(
                    select(JudgmentRequestRecord).where(
                        JudgmentRequestRecord.project_id == self.project_id,
                        func.to_tsvector(
                            "simple",
                            JudgmentRequestRecord.title
                            + " "
                            + JudgmentRequestRecord.decision_question,
                        ).op("@@")(tsquery),
                    )
                )
            )
        values = await self.get_structured_items(tuple(matched_ids))
        allowed = set(allowed_kinds)
        filtered = [item for item in values if not allowed or item.kind in allowed]
        return tuple(filtered[:limit])

    async def list_dependency_items(
        self, target_ids: tuple[UUID, ...]
    ) -> tuple[CopilotStructuredItemData, ...]:
        if not target_ids:
            return ()
        conditions = []
        for name in (
            "judgment_request_id",
            "human_judgment_id",
            "method_model_id",
            "paper_code_mapping_id",
            "mental_model_id",
            "blueprint_eligibility_assessment_id",
            "implementation_blueprint_id",
            "blueprint_section_id",
            "implementation_step_id",
            "verification_check_id",
        ):
            conditions.append(getattr(ArtifactDependencyRecord, name).in_(target_ids))
        rows = await self._rows(
            select(ArtifactDependencyRecord).where(
                ArtifactDependencyRecord.project_id == self.project_id, or_(*conditions)
            )
        )
        return tuple(
            self._item(
                "artifact_dependency",
                row,
                f"{row.dependency_role} ({row.criticality})",
                identity={"artifact_id": str(_first_uuid(row, _ARTIFACT_COLUMNS))},
                state={"criticality": row.criticality, "dependency_role": row.dependency_role},
                provenance={
                    "dependency_id": str(_first_uuid(row, _DEPENDENCY_COLUMNS)),
                    "dependency_type": _first_name(row, _DEPENDENCY_COLUMNS).removeprefix(
                        "dependency_"
                    ),
                },
                material=row.criticality == "blocking",
            )
            for row in rows
        )

    async def list_exact_evidence(
        self, target_ids: tuple[UUID, ...]
    ) -> tuple[CopilotEvidenceItemData, ...]:
        if not target_ids:
            return ()
        rows = await self._rows(
            select(EvidenceLinkRecord).where(
                EvidenceLinkRecord.project_id == self.project_id,
                or_(
                    EvidenceLinkRecord.knowledge_object_id.in_(target_ids),
                    EvidenceLinkRecord.method_element_id.in_(target_ids),
                ),
                EvidenceLinkRecord.lifecycle_status != "superseded",
            )
        )
        results: list[CopilotEvidenceItemData] = []
        for row in rows:
            if row.source_chunk_id is not None:
                chunk = await self._one_evidence(SourceChunkRecord, row.source_chunk_id)
                if chunk is None:
                    continue
                results.append(self._source_chunk_evidence(chunk, evidence=row))
            elif row.repository_component_id is not None:
                component = await self._one_evidence(
                    RepositoryComponentRecord, row.repository_component_id
                )
                if component is None:
                    continue
                results.append(self._repository_evidence(component, evidence=row))
        return tuple(sorted(results, key=lambda item: (item.kind, str(item.id))))

    async def search_source_chunks(
        self, *, query: str, limit: int
    ) -> tuple[CopilotEvidenceItemData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(SourceChunkRecord)
                .join(
                    SourceProcessingRunRecord,
                    SourceProcessingRunRecord.id == SourceChunkRecord.processing_run_id,
                )
                .join(SourceRecord, SourceRecord.id == SourceProcessingRunRecord.source_id)
                .where(
                    SourceRecord.project_id == self.project_id,
                    func.to_tsvector("simple", SourceChunkRecord.chunk_text).op("@@")(
                        func.websearch_to_tsquery("simple", query)
                    ),
                )
                .order_by(
                    SourceChunkRecord.page_number,
                    SourceChunkRecord.chunk_index,
                    SourceChunkRecord.id,
                )
                .limit(limit)
            )
        )
        return tuple(self._source_chunk_evidence(row) for row in result.scalars())

    async def search_repository_components(
        self, *, query: str, limit: int
    ) -> tuple[CopilotEvidenceItemData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(RepositoryComponentRecord)
                .join(
                    RepositorySnapshotRecord,
                    RepositorySnapshotRecord.id == RepositoryComponentRecord.repository_snapshot_id,
                )
                .join(
                    RepositoryRecord,
                    RepositoryRecord.id == RepositorySnapshotRecord.repository_id,
                )
                .where(
                    RepositoryRecord.project_id == self.project_id,
                    func.to_tsvector(
                        "simple",
                        RepositoryComponentRecord.path
                        + " "
                        + RepositoryComponentRecord.content_excerpt,
                    ).op("@@")(func.websearch_to_tsquery("simple", query)),
                )
                .order_by(RepositoryComponentRecord.path, RepositoryComponentRecord.id)
                .limit(limit)
            )
        )
        return tuple(self._repository_evidence(row) for row in result.scalars())

    async def _primary_question_id(self) -> UUID:
        from researchflow.infrastructure.database.models.project import ProjectRecord

        result = await execute_database(
            lambda: self._session.execute(
                select(ProjectRecord.primary_research_question_id).where(
                    ProjectRecord.id == self.project_id
                )
            )
        )
        value = result.scalar_one_or_none()
        if value is None:
            raise ResourceConflictError(details={"reason": "project_context_incomplete"})
        return value

    async def _rows(self, statement: Select[tuple[_RowT]]) -> list[_RowT]:
        result = await execute_database(lambda: self._session.execute(statement))
        return list(result.scalars())

    async def _one_evidence(self, model: type[_RowT], row_id: UUID) -> _RowT | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(model).where(model.id == row_id)  # type: ignore[attr-defined]
            )
        )
        return result.scalar_one_or_none()

    def _knowledge_item(self, row: KnowledgeObjectRecord) -> CopilotStructuredItemData:
        accepted_current = row.review_status == "accepted" and row.lifecycle_status == "current"
        return self._item(
            "knowledge_object",
            row,
            row.statement,
            identity={
                "lineage_id": str(row.lineage_id),
                "version": row.version,
                "object_type": row.object_type,
                "normalized_statement_hash": row.normalized_statement_hash,
            },
            state={
                "review_status": row.review_status,
                "lifecycle_status": row.lifecycle_status,
                "epistemic_status": row.epistemic_status,
                "origin": row.origin,
                "grounding_status": row.grounding_status_cache,
                "accepted_current": accepted_current,
            },
            provenance={
                "grounding_assessment_id": None
                if row.grounding_assessment_id is None
                else str(row.grounding_assessment_id),
                "generation_record_id": None
                if row.generation_record_id is None
                else str(row.generation_record_id),
            },
            material=accepted_current
            or (
                row.object_type in {"ambiguity", "knowledge_gap"}
                and row.lifecycle_status in {"current", "challenged"}
            ),
        )

    def _method_element_item(
        self,
        row: MethodElementRecord,
        *,
        provenance: dict[str, object] | None = None,
    ) -> CopilotStructuredItemData:
        return self._item(
            "method_element",
            row,
            f"{row.element_type} element",
            identity={"lineage_id": str(row.lineage_id), "version": row.version},
            state={
                "review_status": row.review_status,
                "lifecycle_status": row.lifecycle_status,
                "epistemic_status": row.epistemic_status,
                "grounding_status": row.grounding_status_cache,
                "accepted_current": row.review_status == "accepted"
                and row.lifecycle_status == "current",
            },
            provenance=provenance,
        )

    def _step_item(self, row: ImplementationStepRecord) -> CopilotStructuredItemData:
        return self._item(
            "implementation_step",
            row,
            f"{row.title}: {row.description}",
            identity={"lineage_id": str(row.lineage_id), "version": row.version},
            state={
                "lifecycle_status": row.lifecycle_status,
                "freshness_status": row.freshness_status,
            },
            provenance={"blueprint_id": str(row.blueprint_id)},
        )

    def _check_item(self, row: VerificationCheckRecord) -> CopilotStructuredItemData:
        return self._item(
            "verification_check",
            row,
            f"{row.condition} Expected: {row.expected_result}",
            identity={"lineage_id": str(row.lineage_id), "version": row.version},
            state={
                "lifecycle_status": row.lifecycle_status,
                "freshness_status": row.freshness_status,
                "result_status": row.result_status,
                "test_type": row.test_type,
            },
            provenance={
                "blueprint_id": str(row.blueprint_id),
                "implementation_step_id": str(row.implementation_step_id),
            },
        )

    def _item(
        self,
        kind: str,
        row: Any,
        content: str,
        *,
        identity: dict[str, object] | None = None,
        state: dict[str, object] | None = None,
        provenance: dict[str, object] | None = None,
        material: bool = False,
    ) -> CopilotStructuredItemData:
        return CopilotStructuredItemData(
            kind=kind,
            id=row.id,
            project_id=self.project_id,
            content=content,
            identity={"id": str(row.id), **(identity or {})},
            state=state or {},
            provenance=provenance or {},
            material=material,
        )

    def _source_chunk_evidence(
        self, row: SourceChunkRecord, *, evidence: EvidenceLinkRecord | None = None
    ) -> CopilotEvidenceItemData:
        return CopilotEvidenceItemData(
            kind="source_chunk",
            id=row.id,
            project_id=self.project_id,
            content=row.chunk_text,
            content_hash=row.content_hash,
            identity={
                "id": str(row.id),
                "processing_run_id": str(row.processing_run_id),
                "page_number": row.page_number,
                "chunk_index": row.chunk_index,
            },
            state=_evidence_state(evidence),
            provenance={
                "untrusted_evidence": True,
                "evidence_link_id": None if evidence is None else str(evidence.id),
                "relation": None if evidence is None else evidence.evidence_relation,
                "anchor_start_offset": None
                if evidence is None
                else evidence.source_anchor_start_offset,
                "anchor_end_offset": None
                if evidence is None
                else evidence.source_anchor_end_offset,
                "anchor_hash": None if evidence is None else evidence.source_anchor_hash,
            },
        )

    def _repository_evidence(
        self, row: RepositoryComponentRecord, *, evidence: EvidenceLinkRecord | None = None
    ) -> CopilotEvidenceItemData:
        return CopilotEvidenceItemData(
            kind="repository_component",
            id=row.id,
            project_id=self.project_id,
            content=row.content_excerpt,
            content_hash=row.content_hash,
            identity={
                "id": str(row.id),
                "repository_snapshot_id": str(row.repository_snapshot_id),
                "path": row.path,
                "line_start": row.line_start,
                "line_end": row.line_end,
            },
            state=_evidence_state(evidence),
            provenance={
                "untrusted_evidence": True,
                "evidence_link_id": None if evidence is None else str(evidence.id),
                "relation": None if evidence is None else evidence.evidence_relation,
                "inspection_run_id": None
                if row.repository_inspection_run_id is None
                else str(row.repository_inspection_run_id),
            },
        )

    async def _one(self, model: type[_RecordT], row_id: UUID) -> _RecordT | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(model).where(model.id == row_id, model.project_id == self.project_id)
            )
        )
        return result.scalar_one_or_none()


_ARTIFACT_COLUMNS = (
    "judgment_request_id",
    "human_judgment_id",
    "method_model_id",
    "paper_code_mapping_id",
    "mental_model_id",
    "blueprint_eligibility_assessment_id",
    "implementation_blueprint_id",
    "blueprint_section_id",
    "implementation_step_id",
    "verification_check_id",
)
_DEPENDENCY_COLUMNS = (
    "dependency_research_question_id",
    "dependency_research_question_source_id",
    "dependency_source_processing_run_id",
    "dependency_paper_repository_relationship_id",
    "dependency_repository_snapshot_id",
    "dependency_repository_component_id",
    "dependency_knowledge_object_id",
    "dependency_evidence_link_id",
    "dependency_grounding_assessment_id",
    "dependency_method_model_id",
    "dependency_method_model_assessment_id",
    "dependency_method_element_id",
    "dependency_paper_code_mapping_id",
    "dependency_mental_model_id",
    "dependency_mental_model_assessment_id",
    "dependency_human_judgment_id",
    "dependency_blueprint_eligibility_assessment_id",
    "dependency_implementation_blueprint_id",
    "dependency_blueprint_section_id",
    "dependency_implementation_step_id",
    "dependency_verification_check_id",
)


def _first_name(row: object, names: tuple[str, ...]) -> str:
    return next(name for name in names if getattr(row, name) is not None)


def _first_uuid(row: object, names: tuple[str, ...]) -> UUID:
    return cast(UUID, getattr(row, _first_name(row, names)))


def _evidence_state(row: EvidenceLinkRecord | None) -> dict[str, object]:
    if row is None:
        return {"review_status": "raw", "lifecycle_status": "unreviewed", "accepted_current": False}
    return {
        "review_status": row.review_status,
        "lifecycle_status": row.lifecycle_status,
        "accepted_current": row.review_status == "accepted" and row.lifecycle_status == "current",
    }
