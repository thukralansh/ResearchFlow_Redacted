"""Human-confirmed public GitHub repository attachment and immutable snapshots."""

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from researchflow.application.sources import _require_owned
from researchflow.application.staleness import StalenessService
from researchflow.domain.actor import ActorContext
from researchflow.domain.projects import normalize_idempotency_key
from researchflow.domain.repository_authority import (
    confirm_repository_authority,
    normalize_github_url,
    repository_attach_fingerprint,
    repository_attach_identities,
    validate_commit_sha,
    validate_requested_ref,
)
from researchflow.errors import (
    DomainValidationError,
    ExternalServiceError,
    InternalError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from researchflow.ports.github import GitHubGateway, GitHubResolutionError
from researchflow.ports.repositories import (
    NewOperationEvent,
    NewPaperRepositoryRelationship,
    NewRepository,
    NewRepositorySnapshot,
    PaperRepositoryRelationshipData,
    RepositoryData,
    RepositorySnapshotData,
)
from researchflow.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory

_OPERATION = "repository.snapshot.attach"


@dataclass(frozen=True, slots=True)
class AttachRepositoryCommand:
    project_id: UUID
    research_question_id: UUID
    primary_paper_relationship_id: UUID
    source_id: UUID
    repository_url: str
    requested_ref: str | None
    relationship_type: str
    authority_level: str
    rationale: str
    evidence_text: str | None
    confidence: Decimal | None
    human_confirmed: bool
    replace_current: bool = False
    idempotency_key: str | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class RepositorySnapshotView:
    repository: RepositoryData
    snapshot: RepositorySnapshotData
    relationship: PaperRepositoryRelationshipData


def _translate_resolution_error(error: GitHubResolutionError) -> Exception:
    if error.reason == "github_repository_or_ref_not_found":
        return ResourceNotFoundError(details={"resource": "github_repository_or_ref"})
    if error.reason in {"github_repository_private", "github_repository_empty"}:
        return DomainValidationError(details={"reason": error.reason})
    details: dict[str, str | int] = {
        "service_name": "github",
        "operation": error.reason,
    }
    if error.retry_after_seconds is not None:
        details["retry_after_seconds"] = error.retry_after_seconds
    return ExternalServiceError(details=details, cause=error)


class RepositoryApplicationService:
    def __init__(
        self,
        *,
        actor: ActorContext,
        uow_factory: UnitOfWorkFactory,
        github: GitHubGateway,
        staleness_hook: StalenessService | None = None,
    ) -> None:
        self._actor = actor
        self._uow_factory = uow_factory
        self._github = github
        self._staleness_hook = staleness_hook

    async def attach(self, command: AttachRepositoryCommand) -> RepositorySnapshotView:
        normalized = normalize_github_url(command.repository_url)
        requested_ref = validate_requested_ref(command.requested_ref)
        authority = confirm_repository_authority(
            relationship_type=command.relationship_type,
            authority_level=command.authority_level,
            rationale=command.rationale,
            evidence_text=command.evidence_text,
            confidence=command.confidence,
            human_confirmed=command.human_confirmed,
        )
        idempotency_key = normalize_idempotency_key(command.idempotency_key)

        async with self._uow_factory(command.project_id) as uow:
            await self._validate_primary_context(uow, command)
            await uow.rollback()

        try:
            resolved = await self._github.resolve(
                repository_url=normalized.url,
                requested_ref=requested_ref,
            )
        except GitHubResolutionError as error:
            raise _translate_resolution_error(error) from error
        if resolved.repository.is_private:
            raise DomainValidationError(details={"reason": "github_repository_private"})
        if resolved.repository.url != normalized.url:
            raise ResourceConflictError(details={"reason": "github_identity_mismatch"})
        commit_sha = validate_commit_sha(resolved.commit_sha)
        resolved_ref = validate_requested_ref(resolved.requested_ref)
        if resolved_ref is None:
            raise DomainValidationError(details={"reason": "github_ref_invalid"})

        fingerprint = repository_attach_fingerprint(
            {
                "project_id": command.project_id,
                "research_question_id": command.research_question_id,
                "primary_paper_relationship_id": command.primary_paper_relationship_id,
                "source_id": command.source_id,
                "repository_url": normalized.url,
                "requested_ref": requested_ref,
                "resolved_ref": resolved_ref,
                "commit_sha": commit_sha,
                "relationship_type": authority.relationship_type,
                "authority_level": authority.authority_level,
                "rationale": authority.rationale,
                "evidence_text": authority.evidence_text,
                "confidence": authority.confidence,
                "replace_current": command.replace_current,
            }
        )
        identities = repository_attach_identities(
            project_id=command.project_id,
            normalized_url=normalized.url,
            commit_sha=commit_sha,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
        )

        async with self._uow_factory(command.project_id) as uow:
            await self._validate_primary_context(uow, command)
            if idempotency_key is not None:
                event = await uow.events.get_by_idempotency_key(_OPERATION, idempotency_key)
                if event is not None:
                    if event.input_fingerprint != fingerprint:
                        raise ResourceConflictError(
                            details={"reason": "idempotency_fingerprint_mismatch"}
                        )
                    view = await self._load_view(
                        uow,
                        _event_output_id(event.output_refs, "repository"),
                        _event_output_id(event.output_refs, "repository_snapshot"),
                        _event_output_id(event.output_refs, "paper_repository_relationship"),
                    )
                    await uow.rollback()
                    return view

            current = await uow.repositories.get_accepted_current_relationship()
            if current is not None and not command.replace_current:
                existing_repository = await uow.repositories.get_exact(current.repository_id)
                existing_snapshot = (
                    await uow.repositories.get_snapshot_by_commit(current.repository_id, commit_sha)
                    if existing_repository is not None
                    and existing_repository.normalized_url == normalized.url
                    else None
                )
                if (
                    existing_repository is not None
                    and existing_snapshot is not None
                    and current.repository_snapshot_id == existing_snapshot.id
                    and current.source_id == command.source_id
                    and current.relationship_type == authority.relationship_type
                    and current.authority_level == authority.authority_level
                    and current.rationale == authority.rationale
                    and current.evidence_text == authority.evidence_text
                    and current.confidence == authority.confidence
                ):
                    await uow.events.append(
                        _operation_event(
                            event_id=identities.event_id,
                            project_id=command.project_id,
                            actor_id=self._actor.actor_id,
                            source_id=command.source_id,
                            primary_paper_relationship_id=command.primary_paper_relationship_id,
                            repository=existing_repository,
                            snapshot=existing_snapshot,
                            relationship=current,
                            rationale=authority.rationale,
                            idempotency_key=idempotency_key,
                            fingerprint=fingerprint,
                            request_id=command.request_id,
                        )
                    )
                    await uow.commit()
                    return RepositorySnapshotView(existing_repository, existing_snapshot, current)
                raise ResourceConflictError(details={"reason": "repository_replacement_required"})

            repository = await uow.repositories.add(
                NewRepository(
                    id=identities.repository_id,
                    project_id=command.project_id,
                    owner=normalized.owner,
                    name=normalized.name,
                    normalized_url=normalized.url,
                    default_branch=resolved.repository.default_branch or resolved.requested_ref,
                    description=resolved.repository.description,
                    is_private=False,
                )
            )
            predecessor_snapshot_id: UUID | None = None
            lineage_id = identities.relationship_id
            version = 1
            predecessor_relationship_id: UUID | None = None
            if current is not None:
                predecessor_relationship_id = current.id
                lineage_id = current.lineage_id
                version = current.version + 1
                if current.repository_snapshot_id is not None:
                    old_snapshot = await uow.repositories.get_current_snapshot(
                        current.repository_id
                    )
                    if old_snapshot is not None:
                        predecessor_snapshot_id = old_snapshot.id
                        await uow.repositories.mark_snapshot_superseded(old_snapshot.id)
                await uow.repositories.mark_relationship_superseded(
                    current.id, expected_version=current.version
                )

            snapshot = await uow.repositories.add_snapshot(
                NewRepositorySnapshot(
                    id=identities.snapshot_id,
                    repository_id=repository.id,
                    requested_ref=resolved_ref,
                    commit_sha=commit_sha,
                    status="available",
                    lifecycle_status="current",
                    supersedes_id=predecessor_snapshot_id,
                )
            )
            if snapshot.lifecycle_status == "superseded":
                snapshot = await uow.repositories.mark_snapshot_current(snapshot.id)
            relationship = await uow.repositories.add_relationship(
                NewPaperRepositoryRelationship(
                    id=identities.relationship_id,
                    project_id=command.project_id,
                    source_id=command.source_id,
                    repository_id=repository.id,
                    repository_snapshot_id=snapshot.id,
                    lineage_id=lineage_id,
                    version=version,
                    relationship_type=authority.relationship_type,
                    authority_level=authority.authority_level,
                    rationale=authority.rationale,
                    evidence_text=authority.evidence_text,
                    confidence=authority.confidence,
                    review_status="accepted",
                    lifecycle_status="current",
                    created_by_type="human",
                    derived_from_id=predecessor_relationship_id,
                    supersedes_id=predecessor_relationship_id,
                )
            )
            await uow.events.append(
                _operation_event(
                    event_id=identities.event_id,
                    project_id=command.project_id,
                    actor_id=self._actor.actor_id,
                    source_id=command.source_id,
                    primary_paper_relationship_id=command.primary_paper_relationship_id,
                    repository=repository,
                    snapshot=snapshot,
                    relationship=relationship,
                    rationale=authority.rationale,
                    idempotency_key=idempotency_key,
                    fingerprint=fingerprint,
                    request_id=command.request_id,
                )
            )
            if current is not None and self._staleness_hook is not None:
                await self._staleness_hook.artifact_changed(
                    uow=uow,
                    project_id=command.project_id,
                    cause_type="paper_repository_relationship",
                    before_id=current.id,
                    after_id=relationship.id,
                    operation_type="repository.relationship.revised",
                )
                if predecessor_snapshot_id is not None:
                    await self._staleness_hook.artifact_changed(
                        uow=uow,
                        project_id=command.project_id,
                        cause_type="repository_snapshot",
                        before_id=predecessor_snapshot_id,
                        after_id=snapshot.id,
                        operation_type="repository.snapshot.replaced",
                    )
            await uow.commit()
            return RepositorySnapshotView(repository, snapshot, relationship)

    async def _validate_primary_context(
        self, uow: UnitOfWork, command: AttachRepositoryCommand
    ) -> None:
        project = await uow.projects.get()
        _require_owned(self._actor, project)
        question = await uow.research_questions.get_exact(command.research_question_id)
        paper = await uow.sources.get_question_source_exact(command.primary_paper_relationship_id)
        source = await uow.sources.get_exact(command.source_id)
        if source is None:
            raise ResourceNotFoundError(details={"resource": "source"})
        if (
            project is None
            or question is None
            or paper is None
            or project.primary_research_question_id != question.id
            or question.lifecycle_status != "current"
            or paper.research_question_id != question.id
            or paper.source_id != source.id
            or paper.relationship_type != "primary_paper"
            or paper.review_status != "accepted"
            or paper.lifecycle_status != "current"
        ):
            raise ResourceConflictError(details={"reason": "stale_primary_paper_context"})

    @staticmethod
    async def _load_view(
        uow: UnitOfWork, repository_id: UUID, snapshot_id: UUID, relationship_id: UUID
    ) -> RepositorySnapshotView:
        repository = await uow.repositories.get_exact(repository_id)
        snapshot = await uow.repositories.get_snapshot_exact(snapshot_id)
        relationship = await uow.repositories.get_relationship_exact(relationship_id)
        if (
            repository is None
            or snapshot is None
            or snapshot.id != snapshot_id
            or relationship is None
        ):
            raise InternalError(details={"reason": "idempotent_repository_output_missing"})
        return RepositorySnapshotView(repository, snapshot, relationship)


class RepositoryQueryService:
    def __init__(self, *, actor: ActorContext, uow_factory: UnitOfWorkFactory) -> None:
        self._actor = actor
        self._uow_factory = uow_factory

    async def history(self, project_id: UUID) -> tuple[RepositorySnapshotView, ...]:
        async with self._uow_factory(project_id) as uow:
            project = await uow.projects.get()
            _require_owned(self._actor, project)
            relationships = await uow.repositories.list_relationship_history()
            views: list[RepositorySnapshotView] = []
            for relationship in relationships:
                repository = await uow.repositories.get_exact(relationship.repository_id)
                snapshot = (
                    await uow.repositories.get_snapshot_exact(relationship.repository_snapshot_id)
                    if relationship.repository_snapshot_id is not None
                    else None
                )
                if repository is None or snapshot is None:
                    raise InternalError(details={"reason": "repository_history_incomplete"})
                views.append(RepositorySnapshotView(repository, snapshot, relationship))
            await uow.rollback()
            return tuple(views)


def _event_output_id(output_refs: tuple[dict[str, object], ...], output_type: str) -> UUID:
    for reference in output_refs:
        if reference.get("type") == output_type:
            value = reference.get("id")
            try:
                return UUID(str(value))
            except (TypeError, ValueError) as error:
                raise InternalError(
                    details={"reason": "repository_event_output_invalid"}
                ) from error
    raise InternalError(details={"reason": "repository_event_output_missing"})


def _operation_event(
    *,
    event_id: UUID,
    project_id: UUID,
    actor_id: UUID,
    source_id: UUID,
    primary_paper_relationship_id: UUID,
    repository: RepositoryData,
    snapshot: RepositorySnapshotData,
    relationship: PaperRepositoryRelationshipData,
    rationale: str,
    idempotency_key: str | None,
    fingerprint: str,
    request_id: str | None,
) -> NewOperationEvent:
    return NewOperationEvent(
        id=event_id,
        project_id=project_id,
        workflow_run_id=None,
        operation_type=_OPERATION,
        created_by_type="human",
        created_by_id=actor_id,
        input_refs=(
            {"type": "source", "id": str(source_id)},
            {
                "type": "research_question_source",
                "id": str(primary_paper_relationship_id),
            },
        ),
        output_refs=(
            {"type": "repository", "id": str(repository.id)},
            {
                "type": "repository_snapshot",
                "id": str(snapshot.id),
                "commit_sha": snapshot.commit_sha,
            },
            {
                "type": "paper_repository_relationship",
                "id": str(relationship.id),
                "version": relationship.version,
            },
        ),
        reason=rationale,
        idempotency_key=idempotency_key,
        input_fingerprint=fingerprint if idempotency_key is not None else None,
        request_id=request_id,
    )
