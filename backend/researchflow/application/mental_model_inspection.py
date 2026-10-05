"""Read-only, project-scoped inspection of structured Mental Models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from researchflow.domain.actor import ActorContext
from researchflow.errors import ResourceConflictError, ResourceNotFoundError
from researchflow.ports.repositories import (
    ArtifactDependencyData,
    EvidenceLinkData,
    HumanJudgmentData,
    MentalModelData,
    MentalModelMemberData,
    MentalModelRenderingData,
    MethodEvidenceLinkData,
    MethodModelData,
    MethodModelMemberData,
    RepositoryComponentData,
    ResearchQuestionData,
    SourceChunkData,
)
from researchflow.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class EvidenceProvenanceView:
    evidence_type: Literal["knowledge_evidence", "method_evidence"]
    id: UUID
    relation: str
    excerpt: str
    confidence: object | None
    review_status: str
    lifecycle_status: str
    source_chunk: SourceChunkData | None
    repository_component: RepositoryComponentData | None


@dataclass(frozen=True, slots=True)
class MentalModelMemberInspectionView:
    member: MentalModelMemberData
    target_type: Literal["knowledge_object", "human_judgment", "evidence_link"]
    target: dict[str, Any]
    evidence: tuple[EvidenceProvenanceView, ...]


@dataclass(frozen=True, slots=True)
class MethodElementInspectionView:
    member: MethodModelMemberData
    evidence: tuple[EvidenceProvenanceView, ...]


@dataclass(frozen=True, slots=True)
class MentalModelDetailView:
    model: MentalModelData
    is_current: bool
    question: ResearchQuestionData
    method_model: MethodModelData
    method_elements: tuple[MethodElementInspectionView, ...]
    members: tuple[MentalModelMemberInspectionView, ...]
    renderings: tuple[MentalModelRenderingData, ...]
    dependencies: tuple[ArtifactDependencyData, ...]


@dataclass(frozen=True, slots=True)
class MentalModelSummaryView:
    model: MentalModelData
    is_current: bool
    rendering_count: int
    latest_rendering_id: UUID | None


class MentalModelInspectionService:
    """Resolve immutable member references without granting UI mutation authority."""

    def __init__(self, *, actor: ActorContext, uow_factory: UnitOfWorkFactory) -> None:
        self._actor = actor
        self._uow_factory = uow_factory

    async def current(self, project_id: UUID) -> MentalModelDetailView | None:
        async with self._uow_factory(project_id) as uow:
            self._require_owned(await uow.projects.get())
            model = await uow.mental_models.get_current()
            if model is None:
                await uow.rollback()
                return None
            detail = await self._detail(uow, model)
            await uow.rollback()
            return detail

    async def detail(self, project_id: UUID, model_id: UUID) -> MentalModelDetailView:
        async with self._uow_factory(project_id) as uow:
            self._require_owned(await uow.projects.get())
            model = await uow.mental_models.get_exact(model_id)
            if model is None:
                raise ResourceNotFoundError(details={"resource": "mental_model"})
            detail = await self._detail(uow, model)
            await uow.rollback()
            return detail

    async def history(self, project_id: UUID) -> tuple[MentalModelSummaryView, ...]:
        async with self._uow_factory(project_id) as uow:
            self._require_owned(await uow.projects.get())
            current = await uow.mental_models.get_current()
            models = await uow.mental_models.list_history()
            summaries = []
            for model in models:
                renderings = await uow.mental_models.list_renderings(model.id)
                summaries.append(
                    MentalModelSummaryView(
                        model=model,
                        is_current=current is not None and current.id == model.id,
                        rendering_count=len(renderings),
                        latest_rendering_id=renderings[-1].id if renderings else None,
                    )
                )
            await uow.rollback()
            return tuple(summaries)

    async def _detail(self, uow: UnitOfWork, model: MentalModelData) -> MentalModelDetailView:
        current = await uow.mental_models.get_current()
        is_current = current is not None and current.id == model.id
        if model.status == "current" and not is_current:
            raise ResourceConflictError(details={"reason": "mental_model_current_pointer_invalid"})
        question = await uow.research_questions.get_exact(model.research_question_id)
        method = await uow.method_models.get_model_exact(model.method_model_id)
        if question is None or method is None:
            raise ResourceConflictError(details={"reason": "mental_model_dependency_missing"})

        method_elements = []
        for method_member in await uow.method_models.list_members(method.id):
            evidence = tuple(
                [
                    await self._method_evidence(uow, item)
                    for item in await uow.method_models.list_evidence_links(
                        method_member.element.id
                    )
                ]
            )
            method_elements.append(MethodElementInspectionView(method_member, evidence))

        members = tuple(
            [
                await self._member(uow, member)
                for member in await uow.mental_models.list_members(model.id)
            ]
        )
        renderings = await uow.mental_models.list_renderings(model.id)
        dependencies = await uow.dependencies.list_dependencies("mental_model", model.id)
        return MentalModelDetailView(
            model=model,
            is_current=is_current,
            question=question,
            method_model=method,
            method_elements=tuple(method_elements),
            members=members,
            renderings=renderings,
            dependencies=dependencies,
        )

    async def _member(
        self, uow: UnitOfWork, member: MentalModelMemberData
    ) -> MentalModelMemberInspectionView:
        if member.knowledge_object_id is not None:
            knowledge = await uow.knowledge.get_exact(member.knowledge_object_id)
            payload = await uow.knowledge.get_payload(member.knowledge_object_id)
            if knowledge is None or payload is None:
                raise ResourceConflictError(details={"reason": "mental_model_member_missing"})
            evidence = tuple(
                [
                    await self._knowledge_evidence(uow, item)
                    for item in await uow.knowledge.list_evidence_links(knowledge.id)
                ]
            )
            return MentalModelMemberInspectionView(
                member,
                "knowledge_object",
                {
                    "id": knowledge.id,
                    "lineage_id": knowledge.lineage_id,
                    "version": knowledge.version,
                    "object_type": knowledge.object_type,
                    "statement": knowledge.statement,
                    "origin": knowledge.origin,
                    "epistemic_status": knowledge.epistemic_status,
                    "review_status": knowledge.review_status,
                    "lifecycle_status": knowledge.lifecycle_status,
                    "grounding_status": knowledge.grounding_status,
                    "subtype_payload": payload.subtype_payload,
                    "ambiguity_interpretations": payload.ambiguity_interpretations,
                },
                evidence,
            )
        if member.human_judgment_id is not None:
            judgment = await uow.judgments.get_judgment_exact(member.human_judgment_id)
            if judgment is None:
                raise ResourceConflictError(details={"reason": "mental_model_member_missing"})
            return MentalModelMemberInspectionView(
                member,
                "human_judgment",
                _judgment_payload(judgment),
                (),
            )
        if member.evidence_link_id is None:
            raise ResourceConflictError(details={"reason": "mental_model_member_missing"})
        knowledge_link = await uow.knowledge.get_evidence_link_exact(member.evidence_link_id)
        if knowledge_link is not None:
            provenance = await self._knowledge_evidence(uow, knowledge_link)
        else:
            method_link = await uow.method_models.get_evidence_link_exact(member.evidence_link_id)
            if method_link is None:
                raise ResourceConflictError(details={"reason": "mental_model_member_missing"})
            provenance = await self._method_evidence(uow, method_link)
        return MentalModelMemberInspectionView(
            member,
            "evidence_link",
            {"id": provenance.id, "evidence_type": provenance.evidence_type},
            (provenance,),
        )

    async def _knowledge_evidence(
        self, uow: UnitOfWork, link: EvidenceLinkData
    ) -> EvidenceProvenanceView:
        chunk = (
            await uow.sources.get_chunk_exact(link.source_chunk_id)
            if link.source_chunk_id is not None
            else None
        )
        component = (
            await uow.repositories.get_component_exact(link.repository_component_id)
            if link.repository_component_id is not None
            else None
        )
        if (chunk is None) == (component is None):
            raise ResourceConflictError(details={"reason": "evidence_provenance_missing"})
        return EvidenceProvenanceView(
            "knowledge_evidence",
            link.id,
            link.evidence_relation,
            link.evidence_excerpt,
            link.confidence,
            link.review_status,
            link.lifecycle_status,
            chunk,
            component,
        )

    async def _method_evidence(
        self, uow: UnitOfWork, link: MethodEvidenceLinkData
    ) -> EvidenceProvenanceView:
        chunk = (
            await uow.sources.get_chunk_exact(link.source_chunk_id)
            if link.source_chunk_id is not None
            else None
        )
        component = (
            await uow.repositories.get_component_exact(link.repository_component_id)
            if link.repository_component_id is not None
            else None
        )
        if (chunk is None) == (component is None):
            raise ResourceConflictError(details={"reason": "evidence_provenance_missing"})
        return EvidenceProvenanceView(
            "method_evidence",
            link.id,
            link.evidence_relation,
            link.evidence_excerpt,
            link.confidence,
            link.review_status,
            link.lifecycle_status,
            chunk,
            component,
        )

    def _require_owned(self, project: object | None) -> None:
        if project is None or getattr(project, "created_by_id", None) != self._actor.actor_id:
            raise ResourceNotFoundError(details={"resource": "project"})


def _judgment_payload(judgment: HumanJudgmentData) -> dict[str, Any]:
    return {
        "id": judgment.id,
        "lineage_id": judgment.lineage_id,
        "version": judgment.version,
        "judgment_request_id": judgment.judgment_request_id,
        "selected_option_id": judgment.selected_option_id,
        "custom_decision": judgment.custom_decision,
        "rationale": judgment.rationale,
        "confidence": judgment.confidence,
        "lifecycle_status": judgment.lifecycle_status,
    }
