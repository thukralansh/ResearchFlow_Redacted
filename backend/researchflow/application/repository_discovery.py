"""Repository Stage A orchestration with immutable run and snapshot provenance."""

from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from researchflow.application.sources import _require_owned
from researchflow.domain.actor import ActorContext
from researchflow.domain.repository_roles import DiscoveryLimits
from researchflow.errors import (
    InternalError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from researchflow.infrastructure.github.discovery import (
    RepositoryDiscoveryFetchError,
    RepositoryDiscoveryUnsupportedError,
    StructuralDiscoveryPayload,
    discover_repository_structure,
)
from researchflow.ports.github import (
    GitHubProvider,
    GitHubRepository,
    GitHubResolutionError,
)
from researchflow.ports.repositories import (
    NewOperationEvent,
    NewRepositoryComponent,
    NewRepositoryDiscoveryRun,
    NewRepositoryTreeEntry,
    PaperRepositoryRelationshipData,
    RepositoryComponentData,
    RepositoryData,
    RepositoryDiscoveryRunData,
    RepositorySnapshotData,
    RepositoryTreeEntryData,
)
from researchflow.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory

_STARTED = "repository.discovery.started"
_SUCCEEDED = "repository.discovery.succeeded"
_FAILED = "repository.discovery.failed"
_ENTRY_NAMESPACE = uuid5(NAMESPACE_URL, "https://researchflow.local/ids/repository-tree-entry")
_COMPONENT_NAMESPACE = uuid5(
    NAMESPACE_URL, "https://researchflow.local/ids/repository-stage-a-component"
)


@dataclass(frozen=True, slots=True)
class DiscoverRepositoryCommand:
    project_id: UUID
    repository_snapshot_id: UUID
    paper_repository_relationship_id: UUID
    discovery_version: str
    limits: DiscoveryLimits
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class RepositoryDiscoveryView:
    run: RepositoryDiscoveryRunData
    entries: tuple[RepositoryTreeEntryData, ...]
    components: tuple[RepositoryComponentData, ...]


@dataclass(frozen=True, slots=True)
class _DiscoveryContext:
    repository: RepositoryData
    snapshot: RepositorySnapshotData
    relationship: PaperRepositoryRelationshipData


class RepositoryDiscoveryService:
    def __init__(
        self,
        *,
        actor: ActorContext,
        uow_factory: UnitOfWorkFactory,
        github: GitHubProvider,
    ) -> None:
        self._actor = actor
        self._uow_factory = uow_factory
        self._github = github

    async def discover(self, command: DiscoverRepositoryCommand) -> RepositoryDiscoveryView:
        limits = command.limits.validate()
        limit_fingerprint = limits.fingerprint()
        configured = _configured_limits(limits, limit_fingerprint=limit_fingerprint)
        run_id = uuid4()

        async with self._uow_factory(command.project_id) as uow:
            context = await self._validate_context(uow, command)
            existing = await uow.repositories.get_successful_discovery_run(
                repository_snapshot_id=command.repository_snapshot_id,
                discovery_version=command.discovery_version,
                limit_fingerprint=limit_fingerprint,
            )
            if existing is not None:
                view = await _load_view(uow, existing)
                await uow.rollback()
                return view
            run = await uow.repositories.add_discovery_run(
                NewRepositoryDiscoveryRun(
                    id=run_id,
                    repository_snapshot_id=command.repository_snapshot_id,
                    discovery_version=command.discovery_version,
                    configured_limits=configured,
                )
            )
            await uow.events.append(
                _event(
                    project_id=command.project_id,
                    actor_id=self._actor.actor_id,
                    operation_type=_STARTED,
                    run=run,
                    relationship_id=command.paper_repository_relationship_id,
                    reason="Bounded path-only Repository Stage A started.",
                    request_id=command.request_id,
                )
            )
            await uow.commit()

        repository = GitHubRepository(
            url=context.repository.normalized_url,
            owner=context.repository.owner,
            name=context.repository.name,
            default_branch=context.repository.default_branch,
            description=context.repository.description,
            is_private=context.repository.is_private,
        )
        try:
            payload = await discover_repository_structure(
                github=self._github,
                repository=repository,
                commit_sha=context.snapshot.commit_sha,
                limits=limits,
            )
        except RepositoryDiscoveryUnsupportedError as error:
            return await self._fail(
                command,
                run_id,
                configured,
                error_code=error.reason,
                error_detail="Repository tree is unsupported by the configured Stage A cap.",
            )
        except RepositoryDiscoveryFetchError as error:
            failed_metrics = {
                **configured,
                "request_count": error.request_count,
                "response_bytes": error.response_bytes,
                "content_bytes": error.content_bytes,
            }
            detail = (
                f"retry_after_seconds={error.retry_after_seconds}"
                if error.retry_after_seconds is not None
                else None
            )
            return await self._fail(
                command,
                run_id,
                failed_metrics,
                error_code=f"repository_discovery_{error.reason}",
                error_detail=detail,
            )
        except GitHubResolutionError as error:
            return await self._fail(
                command,
                run_id,
                configured,
                error_code=f"repository_discovery_{error.reason}",
                error_detail=None,
            )

        try:
            return await self._complete(command, run_id, configured, payload)
        except ResourceConflictError:
            await self._fail(
                command,
                run_id,
                configured,
                error_code="repository_discovery_stale_context",
                error_detail=None,
            )
            raise

    async def _complete(
        self,
        command: DiscoverRepositoryCommand,
        run_id: UUID,
        configured: dict[str, object],
        payload: StructuralDiscoveryPayload,
    ) -> RepositoryDiscoveryView:
        observed = {
            **configured,
            "request_count": payload.request_count,
            "response_bytes": payload.response_bytes,
            "content_bytes": payload.content_bytes,
            "tree_entry_count": len(payload.entries),
            "selected_path_count": len(payload.documents),
            "excluded_path_count": sum(
                entry.excluded_reason is not None for entry in payload.entries
            ),
        }
        async with self._uow_factory(command.project_id) as uow:
            await self._validate_context(uow, command)
            run = await uow.repositories.get_discovery_run_exact(run_id)
            if run is None or run.status != "running":
                raise ResourceConflictError(details={"reason": "stale_repository_discovery_run"})
            entries = await uow.repositories.add_tree_entries(
                tuple(
                    NewRepositoryTreeEntry(
                        id=uuid5(_ENTRY_NAMESPACE, f"{run_id}:{entry.path}"),
                        discovery_run_id=run_id,
                        path=entry.path,
                        entry_type=entry.entry_type,
                        language=entry.language,
                        coarse_role=entry.coarse_role,
                        size_bytes=entry.size_bytes,
                        excluded_reason=entry.excluded_reason,
                    )
                    for entry in payload.entries
                )
            )
            components = await uow.repositories.add_components(
                tuple(
                    NewRepositoryComponent(
                        id=uuid5(_COMPONENT_NAMESPACE, f"{run_id}:{document.path}"),
                        repository_snapshot_id=command.repository_snapshot_id,
                        repository_discovery_run_id=run_id,
                        path=document.path,
                        component_type=document.component_type,
                        language=document.language,
                        line_start=document.line_start,
                        line_end=document.line_end,
                        content_excerpt=document.content_excerpt,
                        content_hash=document.content_hash,
                    )
                    for document in payload.documents
                )
            )
            run = await uow.repositories.finish_discovery_run(
                run_id,
                expected_status="running",
                status="succeeded",
                coverage_status=payload.coverage_status,
                configured_limits=observed,
                tree_hash=payload.tree_hash,
            )
            await uow.events.append(
                _event(
                    project_id=command.project_id,
                    actor_id=self._actor.actor_id,
                    operation_type=_SUCCEEDED,
                    run=run,
                    relationship_id=command.paper_repository_relationship_id,
                    reason=(
                        "Stage A structural discovery completed with "
                        f"{payload.coverage_status} coverage."
                    ),
                    request_id=command.request_id,
                )
            )
            await uow.commit()
            return RepositoryDiscoveryView(
                run,
                tuple(sorted(entries, key=lambda entry: (entry.path.casefold(), entry.path))),
                tuple(
                    sorted(
                        components,
                        key=lambda component: (component.path.casefold(), component.path),
                    )
                ),
            )

    async def _fail(
        self,
        command: DiscoverRepositoryCommand,
        run_id: UUID,
        configured: dict[str, object],
        *,
        error_code: str,
        error_detail: str | None,
    ) -> RepositoryDiscoveryView:
        async with self._uow_factory(command.project_id) as uow:
            project = await uow.projects.get()
            _require_owned(self._actor, project)
            run = await uow.repositories.get_discovery_run_exact(run_id)
            if run is None:
                raise InternalError(details={"reason": "repository_discovery_run_missing"})
            if run.status == "running":
                run = await uow.repositories.finish_discovery_run(
                    run_id,
                    expected_status="running",
                    status="failed",
                    coverage_status="partial",
                    configured_limits=configured,
                    tree_hash=None,
                    error_code=error_code,
                    error_detail=error_detail,
                )
                await uow.events.append(
                    _event(
                        project_id=command.project_id,
                        actor_id=self._actor.actor_id,
                        operation_type=_FAILED,
                        run=run,
                        relationship_id=command.paper_repository_relationship_id,
                        reason=error_code,
                        request_id=command.request_id,
                    )
                )
                await uow.commit()
            else:
                await uow.rollback()
            return RepositoryDiscoveryView(run, (), ())

    async def _validate_context(
        self, uow: UnitOfWork, command: DiscoverRepositoryCommand
    ) -> _DiscoveryContext:
        project = await uow.projects.get()
        _require_owned(self._actor, project)
        snapshot = await uow.repositories.get_snapshot_exact(command.repository_snapshot_id)
        relationship = await uow.repositories.get_relationship_exact(
            command.paper_repository_relationship_id
        )
        current = await uow.repositories.get_accepted_current_relationship()
        repository = (
            await uow.repositories.get_exact(snapshot.repository_id)
            if snapshot is not None
            else None
        )
        if snapshot is None or repository is None:
            raise ResourceNotFoundError(details={"resource": "repository_snapshot"})
        if (
            current is None
            or relationship is None
            or current.id != relationship.id
            or relationship.repository_snapshot_id != snapshot.id
            or relationship.repository_id != repository.id
            or relationship.review_status != "accepted"
            or relationship.lifecycle_status != "current"
            or snapshot.status != "available"
            or snapshot.lifecycle_status != "current"
            or repository.is_private
        ):
            raise ResourceConflictError(details={"reason": "stale_repository_context"})
        return _DiscoveryContext(repository, snapshot, relationship)


class RepositoryDiscoveryQueryService:
    def __init__(self, *, actor: ActorContext, uow_factory: UnitOfWorkFactory) -> None:
        self._actor = actor
        self._uow_factory = uow_factory

    async def history(self, project_id: UUID) -> tuple[RepositoryDiscoveryView, ...]:
        async with self._uow_factory(project_id) as uow:
            _require_owned(self._actor, await uow.projects.get())
            runs = await uow.repositories.list_discovery_runs()
            views = tuple([await _load_view(uow, run) for run in runs])
            await uow.rollback()
            return views

    async def detail(self, project_id: UUID, run_id: UUID) -> RepositoryDiscoveryView:
        async with self._uow_factory(project_id) as uow:
            _require_owned(self._actor, await uow.projects.get())
            run = await uow.repositories.get_discovery_run_exact(run_id)
            if run is None:
                raise ResourceNotFoundError(details={"resource": "repository_discovery_run"})
            view = await _load_view(uow, run)
            await uow.rollback()
            return view


async def _load_view(uow: UnitOfWork, run: RepositoryDiscoveryRunData) -> RepositoryDiscoveryView:
    entries = await uow.repositories.list_tree_entries(run.id)
    components = await uow.repositories.list_discovery_components(run.id)
    return RepositoryDiscoveryView(run, entries, components)


def _configured_limits(limits: DiscoveryLimits, *, limit_fingerprint: str) -> dict[str, object]:
    return {
        **limits.identity(),
        "limit_fingerprint": limit_fingerprint,
        "request_count": 0,
        "response_bytes": 0,
        "content_bytes": 0,
        "tree_entry_count": 0,
        "selected_path_count": 0,
        "excluded_path_count": 0,
    }


def _event(
    *,
    project_id: UUID,
    actor_id: UUID,
    operation_type: str,
    run: RepositoryDiscoveryRunData,
    relationship_id: UUID,
    reason: str,
    request_id: str | None,
) -> NewOperationEvent:
    return NewOperationEvent(
        id=uuid4(),
        project_id=project_id,
        workflow_run_id=None,
        operation_type=operation_type,
        created_by_type="human",
        created_by_id=actor_id,
        input_refs=(
            {"type": "repository_snapshot", "id": str(run.repository_snapshot_id)},
            {"type": "paper_repository_relationship", "id": str(relationship_id)},
        ),
        output_refs=(
            {
                "type": "repository_discovery_run",
                "id": str(run.id),
                "status": run.status,
            },
        ),
        reason=reason,
        request_id=request_id,
    )
