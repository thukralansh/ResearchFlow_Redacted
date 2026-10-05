"""Applicable knowledge-guided Repository Stage B inspection orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from researchflow.application.sources import _require_owned
from researchflow.domain.actor import ActorContext
from researchflow.domain.repository_ranking import (
    BYTE_BUDGET_POLICY_VERSION,
    GuidanceInput,
    InspectionLimits,
    RankingWeights,
    RepositoryGuidance,
    expected_roles_for_element,
)
from researchflow.errors import (
    DomainValidationError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from researchflow.infrastructure.github.python_symbols import (
    ExtractedRepositoryComponent,
    extract_file_component,
    extract_python_components,
)
from researchflow.infrastructure.github.ranking import (
    RankedRepositoryPath,
    rank_repository_paths,
)
from researchflow.ports.github import GitHubProvider, GitHubRepository, GitHubResolutionError
from researchflow.ports.repositories import (
    NewOperationEvent,
    NewRepositoryInspectionComponent,
    NewRepositoryInspectionRun,
    PaperRepositoryRelationshipData,
    RepositoryComponentData,
    RepositoryData,
    RepositoryDiscoveryRunData,
    RepositoryInspectionRunData,
    RepositorySnapshotData,
)
from researchflow.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory

_SUCCEEDED = "repository.inspection.succeeded"
_NOT_APPLICABLE = "repository.inspection.not_applicable"
_COMPONENT_NAMESPACE = uuid5(
    NAMESPACE_URL, "https://researchflow.local/ids/repository-stage-b-component"
)


@dataclass(frozen=True, slots=True)
class InspectRepositoryCommand:
    project_id: UUID
    applicable: bool
    applicability_rationale: str
    repository_snapshot_id: UUID | None
    paper_repository_relationship_id: UUID | None
    discovery_run_id: UUID | None
    ranking_version: str
    analysis_version: str
    limits: InspectionLimits
    weights: RankingWeights
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class RepositoryInspectionView:
    outcome: str
    applicability_rationale: str
    run: RepositoryInspectionRunData | None
    components: tuple[RepositoryComponentData, ...]
    ranking: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class _InspectionContext:
    repository: RepositoryData
    snapshot: RepositorySnapshotData
    relationship: PaperRepositoryRelationshipData
    discovery: RepositoryDiscoveryRunData
    guidance: RepositoryGuidance
    ranking: tuple[RankedRepositoryPath, ...]
    fingerprint: str


@dataclass(frozen=True, slots=True)
class _FetchedPath:
    ranked: RankedRepositoryPath
    components: tuple[ExtractedRepositoryComponent, ...]
    warning: str | None
    response_bytes: int
    content_bytes: int


class RepositoryInspectionService:
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

    async def inspect(self, command: InspectRepositoryCommand) -> RepositoryInspectionView:
        rationale = command.applicability_rationale.strip()
        if not rationale:
            raise DomainValidationError(details={"reason": "applicability_rationale_required"})
        limits = command.limits.validate()
        weights = command.weights.validate()
        if not command.applicable:
            return await self._record_not_applicable(command, rationale)
        if (
            command.repository_snapshot_id is None
            or command.paper_repository_relationship_id is None
            or command.discovery_run_id is None
        ):
            raise DomainValidationError(details={"reason": "repository_inspection_inputs_required"})

        async with self._uow_factory(command.project_id) as uow:
            context = await self._load_context(uow, command, limits=limits, weights=weights)
            existing = await uow.repositories.get_successful_inspection_run(
                repository_snapshot_id=context.snapshot.id,
                discovery_run_id=context.discovery.id,
                guidance_fingerprint=context.fingerprint,
                ranking_version=command.ranking_version,
                analysis_version=command.analysis_version,
            )
            if existing is not None:
                view = await _load_view(uow, existing, rationale)
                await uow.rollback()
                return view
            await uow.rollback()

        fetched = await self._fetch(context, limits)
        async with self._uow_factory(command.project_id) as uow:
            current = await self._load_context(uow, command, limits=limits, weights=weights)
            if current.fingerprint != context.fingerprint:
                raise ResourceConflictError(
                    details={"reason": "repository_inspection_stale_context"}
                )
            existing = await uow.repositories.get_successful_inspection_run(
                repository_snapshot_id=current.snapshot.id,
                discovery_run_id=current.discovery.id,
                guidance_fingerprint=current.fingerprint,
                ranking_version=command.ranking_version,
                analysis_version=command.analysis_version,
            )
            if existing is not None:
                view = await _load_view(uow, existing, rationale)
                await uow.rollback()
                return view
            run_id = uuid4()
            diagnostics = [item.diagnostic() for item in current.ranking[: limits.candidate_limit]]
            warnings = [
                {"path": item.ranked.path, "warning": item.warning}
                for item in fetched
                if item.warning is not None
            ]
            selected_paths = tuple(item.path for item in current.ranking[: limits.candidate_limit])
            inspected_paths = tuple(item.ranked.path for item in fetched)
            configured = {
                **limits.identity(),
                "byte_budget_policy_version": BYTE_BUDGET_POLICY_VERSION,
                "weights": weights.identity(),
                "guidance_context": {
                    "research_question_id": current.guidance.research_question_id,
                    "method_model_id": current.guidance.method_model_id,
                    "mental_model_id": current.guidance.mental_model_id,
                    "relationship_type": current.guidance.relationship_type,
                    "authority_level": current.guidance.authority_level,
                    "accepted_input_count": len(current.guidance.accepted_inputs),
                },
                "ranking_diagnostics": diagnostics,
                "warnings": warnings,
                "request_count": len(fetched),
                "response_bytes": sum(item.response_bytes for item in fetched),
                "content_bytes": sum(item.content_bytes for item in fetched),
                "candidate_count": min(len(current.ranking), limits.candidate_limit),
                "inspected_path_count": len(fetched),
            }
            coverage = _coverage_status(current.ranking, fetched, limits)
            run = await uow.repositories.add_inspection_run(
                NewRepositoryInspectionRun(
                    id=run_id,
                    repository_snapshot_id=current.snapshot.id,
                    discovery_run_id=current.discovery.id,
                    guidance_fingerprint=current.fingerprint,
                    ranking_version=command.ranking_version,
                    analysis_version=command.analysis_version,
                    configured_limits=configured,
                    selected_paths=selected_paths,
                    inspected_paths=inspected_paths,
                    coverage_status=coverage,
                )
            )
            components = await uow.repositories.add_inspection_components(
                tuple(
                    NewRepositoryInspectionComponent(
                        id=uuid5(
                            _COMPONENT_NAMESPACE,
                            f"{run_id}:{path.ranked.path}:{component.symbol or ''}:"
                            f"{component.line_start}",
                        ),
                        repository_snapshot_id=current.snapshot.id,
                        repository_inspection_run_id=run_id,
                        path=path.ranked.path,
                        component_type=component.component_type,
                        language=path.ranked.language,
                        symbol=component.symbol,
                        line_start=component.line_start,
                        line_end=component.line_end,
                        content_excerpt=component.content_excerpt,
                        content_hash=component.content_hash,
                        relevance_score=path.ranked.score,
                    )
                    for path in fetched
                    for component in path.components
                )
            )
            await uow.events.append(
                _event(
                    command=command,
                    actor_id=self._actor.actor_id,
                    operation_type=_SUCCEEDED,
                    reason=f"Stage B completed with {coverage} bounded coverage.",
                    run_id=run.id,
                    fingerprint=run.guidance_fingerprint,
                )
            )
            await uow.commit()
            return RepositoryInspectionView(
                outcome="applicable",
                applicability_rationale=rationale,
                run=run,
                components=components,
                ranking=tuple(diagnostics),
            )

    async def _record_not_applicable(
        self, command: InspectRepositoryCommand, rationale: str
    ) -> RepositoryInspectionView:
        if any(
            value is not None
            for value in (
                command.repository_snapshot_id,
                command.paper_repository_relationship_id,
                command.discovery_run_id,
            )
        ):
            raise DomainValidationError(details={"reason": "not_applicable_repository_inputs"})
        async with self._uow_factory(command.project_id) as uow:
            _require_owned(self._actor, await uow.projects.get())
            question = await uow.research_questions.get_current_primary()
            if question is None:
                raise ResourceConflictError(details={"reason": "current_primary_question_required"})
            await uow.events.append(
                _event(
                    command=command,
                    actor_id=self._actor.actor_id,
                    operation_type=_NOT_APPLICABLE,
                    reason=rationale,
                    run_id=None,
                    fingerprint=None,
                )
            )
            await uow.commit()
        return RepositoryInspectionView("not_applicable", rationale, None, (), ())

    async def _load_context(
        self,
        uow: UnitOfWork,
        command: InspectRepositoryCommand,
        *,
        limits: InspectionLimits,
        weights: RankingWeights,
    ) -> _InspectionContext:
        project = await uow.projects.get()
        _require_owned(self._actor, project)
        if project is None:  # pragma: no cover - narrowed by _require_owned
            raise ResourceNotFoundError(details={"resource": "project"})
        question = await uow.research_questions.get_current_primary()
        primary_paper = await uow.sources.get_current_primary_paper()
        relationship = await uow.repositories.get_accepted_current_relationship()
        snapshot = (
            await uow.repositories.get_snapshot_exact(command.repository_snapshot_id)
            if command.repository_snapshot_id is not None
            else None
        )
        discovery = (
            await uow.repositories.get_discovery_run_exact(command.discovery_run_id)
            if command.discovery_run_id is not None
            else None
        )
        repository = (
            await uow.repositories.get_exact(snapshot.repository_id)
            if snapshot is not None
            else None
        )
        method_model = (
            await uow.method_models.get_accepted_current_model(question.id)
            if question is not None
            else None
        )
        mental_model = await uow.mental_models.get_current()
        if question is None or primary_paper is None or primary_paper.review_status != "accepted":
            raise ResourceConflictError(details={"reason": "primary_source_review_required"})
        if (
            method_model is None
            or method_model.current_assessment_id is None
            or method_model.freshness_status != "current"
            or mental_model is None
            or mental_model.method_model_id != method_model.id
        ):
            raise ResourceConflictError(details={"reason": "current_initial_model_required"})
        if (
            relationship is None
            or relationship.id != command.paper_repository_relationship_id
            or snapshot is None
            or relationship.repository_snapshot_id != snapshot.id
            or discovery is None
            or discovery.repository_snapshot_id != snapshot.id
            or discovery.status != "succeeded"
            or discovery.tree_hash is None
            or repository is None
            or repository.is_private
            or snapshot.status != "available"
            or snapshot.lifecycle_status != "current"
        ):
            raise ResourceConflictError(details={"reason": "stale_repository_context"})

        knowledge = await uow.knowledge.list_accepted_current()
        members = await uow.method_models.list_members(method_model.id)
        mental_members = await uow.mental_models.list_members(mental_model.id)
        inputs: list[GuidanceInput] = [
            GuidanceInput(
                "method_model",
                str(method_model.id),
                f"{method_model.name} {method_model.objective}",
                ("model_definition",),
            )
        ]
        inputs.extend(
            GuidanceInput("knowledge_object", str(item.id), item.statement)
            for item in knowledge
            if item.object_type in {"claim", "assumption", "implementation_requirement"}
        )
        inputs.extend(
            GuidanceInput(
                "method_element",
                str(member.element.id),
                " ".join(_flatten_text(member.subtype_payload)),
                expected_roles_for_element(member.element.element_type),
            )
            for member in members
            if member.element.review_status == "accepted"
            and member.element.lifecycle_status == "current"
        )
        inputs.extend(
            GuidanceInput(
                "mental_model_member",
                str(member.id),
                f"{member.role} {member.inclusion_rationale}",
            )
            for member in mental_members
        )
        guidance = RepositoryGuidance(
            project_id=str(project.id),
            research_question_id=str(question.id),
            question_text=question.question_text,
            objective=" ".join(
                value for value in (project.target_outcome, project.description) if value
            ),
            scope=question.scope_text,
            research_depth=project.reproduction_depth,
            method_model_id=str(method_model.id),
            method_model_fingerprint=method_model.composition_fingerprint,
            mental_model_id=str(mental_model.id),
            mental_model_fingerprint=mental_model.composition_fingerprint,
            relationship_id=str(relationship.id),
            relationship_type=relationship.relationship_type,
            authority_level=relationship.authority_level,
            discovery_run_id=str(discovery.id),
            discovery_tree_hash=discovery.tree_hash,
            accepted_inputs=tuple(inputs),
        )
        fingerprint = guidance.fingerprint(
            ranking_version=command.ranking_version,
            analysis_version=command.analysis_version,
            byte_budget_policy_version=BYTE_BUDGET_POLICY_VERSION,
            weights=weights,
            limits=limits,
        )
        entries = await uow.repositories.list_tree_entries(discovery.id)
        components = await uow.repositories.list_discovery_components(discovery.id)
        ranking = rank_repository_paths(
            entries=entries,
            stage_a_components=components,
            guidance=guidance,
            weights=weights,
            limits=limits,
        )
        if not ranking:
            raise ResourceConflictError(details={"reason": "no_relevant_repository_files"})
        return _InspectionContext(
            repository, snapshot, relationship, discovery, guidance, ranking, fingerprint
        )

    async def _fetch(
        self, context: _InspectionContext, limits: InspectionLimits
    ) -> tuple[_FetchedPath, ...]:
        repository = GitHubRepository(
            url=context.repository.normalized_url,
            owner=context.repository.owner,
            name=context.repository.name,
            default_branch=context.repository.default_branch,
            description=context.repository.description,
            is_private=context.repository.is_private,
        )
        fetched: list[_FetchedPath] = []
        actual_bytes = 0
        for ranked in (item for item in context.ranking if item.selected):
            try:
                response = await self._github.file(
                    repository=repository,
                    commit_sha=context.snapshot.commit_sha,
                    path=ranked.path,
                )
            except GitHubResolutionError as error:
                if error.reason == "github_repository_or_ref_not_found":
                    raise ResourceConflictError(
                        details={
                            "reason": "repository_file_missing_at_pinned_sha",
                            "path": ranked.path,
                        }
                    ) from error
                raise
            if len(response.content) > limits.max_file_bytes:
                raise ResourceConflictError(
                    details={"reason": "repository_file_oversized", "path": ranked.path}
                )
            actual_bytes += len(response.content)
            if actual_bytes > limits.aggregate_bytes:
                raise ResourceConflictError(
                    details={"reason": "repository_aggregate_bytes_exceeded"}
                )
            try:
                extraction = (
                    extract_python_components(response.content)
                    if ranked.language == "Python"
                    else extract_file_component(response.content)
                )
            except UnicodeDecodeError as error:
                raise ResourceConflictError(
                    details={"reason": "repository_file_decode_failed", "path": ranked.path}
                ) from error
            fetched.append(
                _FetchedPath(
                    ranked=ranked,
                    components=extraction.components,
                    warning=extraction.warning,
                    response_bytes=response.response_bytes,
                    content_bytes=len(response.content),
                )
            )
        return tuple(fetched)


class RepositoryInspectionQueryService:
    def __init__(self, *, actor: ActorContext, uow_factory: UnitOfWorkFactory) -> None:
        self._actor = actor
        self._uow_factory = uow_factory

    async def history(self, project_id: UUID) -> tuple[RepositoryInspectionView, ...]:
        async with self._uow_factory(project_id) as uow:
            _require_owned(self._actor, await uow.projects.get())
            runs = await uow.repositories.list_inspection_runs()
            views = tuple(
                [await _load_view(uow, run, "Recorded applicable branch.") for run in runs]
            )
            await uow.rollback()
            return views

    async def detail(self, project_id: UUID, run_id: UUID) -> RepositoryInspectionView:
        async with self._uow_factory(project_id) as uow:
            _require_owned(self._actor, await uow.projects.get())
            run = await uow.repositories.get_inspection_run_exact(run_id)
            if run is None:
                raise ResourceNotFoundError(details={"resource": "repository_inspection_run"})
            view = await _load_view(uow, run, "Recorded applicable branch.")
            await uow.rollback()
            return view


async def _load_view(
    uow: UnitOfWork, run: RepositoryInspectionRunData, rationale: str
) -> RepositoryInspectionView:
    components = await uow.repositories.list_inspection_components(run.id)
    diagnostics = run.configured_limits.get("ranking_diagnostics", [])
    ranking = (
        tuple(item for item in diagnostics if isinstance(item, dict))
        if isinstance(diagnostics, list)
        else ()
    )
    return RepositoryInspectionView("applicable", rationale, run, components, ranking)


def _flatten_text(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, dict):
        return tuple(text for key in sorted(value) for text in _flatten_text(value[key]))
    if isinstance(value, (list, tuple)):
        return tuple(text for item in value for text in _flatten_text(item))
    return ()


def _coverage_status(
    ranking: tuple[RankedRepositoryPath, ...],
    fetched: tuple[_FetchedPath, ...],
    limits: InspectionLimits,
) -> str:
    eligible_count = len(ranking)
    selected_count = len(fetched)
    return (
        "complete"
        if eligible_count <= limits.inspection_limit
        and selected_count == eligible_count
        and not any(item.warning for item in fetched)
        else "partial"
    )


def _event(
    *,
    command: InspectRepositoryCommand,
    actor_id: UUID,
    operation_type: str,
    reason: str,
    run_id: UUID | None,
    fingerprint: str | None,
) -> NewOperationEvent:
    inputs: list[dict[str, object]] = [
        {"type": "repository_branch_applicability", "applicable": command.applicable}
    ]
    for ref_type, value in (
        ("repository_snapshot", command.repository_snapshot_id),
        ("paper_repository_relationship", command.paper_repository_relationship_id),
        ("repository_discovery_run", command.discovery_run_id),
    ):
        if value is not None:
            inputs.append({"type": ref_type, "id": str(value)})
    outputs: tuple[dict[str, object], ...] = (
        ({"type": "repository_inspection_run", "id": str(run_id), "fingerprint": fingerprint},)
        if run_id is not None
        else ({"type": "repository_branch_outcome", "outcome": "not_applicable"},)
    )
    return NewOperationEvent(
        id=uuid4(),
        project_id=command.project_id,
        workflow_run_id=None,
        operation_type=operation_type,
        created_by_type="human",
        created_by_id=actor_id,
        input_refs=tuple(inputs),
        output_refs=outputs,
        reason=reason,
        request_id=command.request_id,
    )
