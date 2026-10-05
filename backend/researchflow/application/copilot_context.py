"""Deterministic FS-COP-02 project context and bounded evidence retrieval."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, is_dataclass
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from researchflow.domain.actor import ActorContext
from researchflow.domain.copilot_context import (
    COPILOT_BOUNDS,
    COPILOT_BOUNDS_POLICY_VERSION,
    COPILOT_CONTEXT_POLICY_VERSION,
    COPILOT_RETRIEVAL_POLICY_VERSION,
    CopilotManifestKind,
    CopilotRetrievalIntent,
    validate_retrieval_selector,
)
from researchflow.errors import DomainValidationError, ResourceConflictError, ResourceNotFoundError
from researchflow.ports.copilot import (
    CopilotEvidenceItemData,
    CopilotManifestData,
    CopilotStructuredItemData,
    NewCopilotManifest,
)
from researchflow.ports.repositories import ProjectData, ResearchQuestionData
from researchflow.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory

_NAMESPACE = uuid5(NAMESPACE_URL, "https://researchflow.local/fs-cop-02")


@dataclass(frozen=True, slots=True)
class BuildCopilotContextCommand:
    project_id: UUID


@dataclass(frozen=True, slots=True)
class BuildCopilotRetrievalCommand:
    project_id: UUID
    context_manifest_id: UUID
    intent: str
    query: str | None = None
    target_ids: tuple[UUID, ...] = ()
    allowed_kinds: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _Selection:
    selector: dict[str, object]
    items: tuple[dict[str, object], ...]
    limitations: tuple[dict[str, object], ...]
    truncated: bool


class CopilotContextService:
    """Build immutable manifests without provider or canonical-state mutation."""

    def __init__(self, *, actor: ActorContext, uow_factory: UnitOfWorkFactory) -> None:
        self._actor = actor
        self._uow_factory = uow_factory

    async def build_context(self, command: BuildCopilotContextCommand) -> CopilotManifestData:
        async with self._uow_factory(command.project_id) as uow:
            await uow.configure_consistent_snapshot(read_only=True)
            selection = await self._select_context(uow)
            await uow.rollback()
        return await self._persist_revalidated(
            command.project_id,
            kind=CopilotManifestKind.CONTEXT,
            parent=None,
            policy_version=COPILOT_CONTEXT_POLICY_VERSION,
            expected=selection,
            selector=lambda uow: self._select_context(uow),
        )

    async def build_retrieval(self, command: BuildCopilotRetrievalCommand) -> CopilotManifestData:
        validate_retrieval_selector(
            intent=command.intent,
            query=command.query,
            target_ids=tuple(str(value) for value in command.target_ids),
            allowed_kinds=command.allowed_kinds,
        )
        async with self._uow_factory(command.project_id) as uow:
            await uow.configure_consistent_snapshot(read_only=True)
            await self._authorize(uow)
            parent = await uow.copilot.get_manifest(command.context_manifest_id)
            if parent is None or parent.kind != CopilotManifestKind.CONTEXT.value:
                raise ResourceNotFoundError(details={"resource": "copilot_context_manifest"})
            selection = await self._select_retrieval(uow, command)
            await uow.rollback()
        return await self._persist_revalidated(
            command.project_id,
            kind=CopilotManifestKind.RETRIEVAL,
            parent=parent,
            policy_version=COPILOT_RETRIEVAL_POLICY_VERSION,
            expected=selection,
            selector=lambda uow: self._select_retrieval(uow, command),
        )

    async def get_manifest(self, project_id: UUID, manifest_id: UUID) -> CopilotManifestData:
        async with self._uow_factory(project_id) as uow:
            await uow.configure_consistent_snapshot(read_only=True)
            await self._authorize(uow)
            manifest = await uow.copilot.get_manifest(manifest_id)
            await uow.rollback()
        if manifest is None:
            raise ResourceNotFoundError(details={"resource": "copilot_manifest"})
        return manifest

    async def _select_context(self, uow: UnitOfWork) -> _Selection:
        project, question = await self._authorize(uow)
        candidates = await uow.copilot.list_context_items(question.id)
        material = [item for item in candidates if item.material]
        if len(material) > COPILOT_BOUNDS.structured_objects:
            raise DomainValidationError(
                details={"reason": "material_context_exceeds_bound", "count": len(material)}
            )
        selected = sorted(candidates, key=_context_order)[: COPILOT_BOUNDS.structured_objects]
        selected_ids = {item.id for item in selected}
        omitted = [item for item in candidates if item.id not in selected_ids]
        if any(item.material for item in omitted):
            raise DomainValidationError(details={"reason": "material_context_omitted"})
        limitations: list[dict[str, object]] = []
        if omitted:
            limitations.append(
                {
                    "code": "structured_context_truncated",
                    "omitted_count": len(omitted),
                    "material_omitted": False,
                }
            )
        if not any(item.kind == "repository_relationship" for item in candidates):
            limitations.append(
                {
                    "code": "repository_evidence_absent_or_not_applicable",
                    "message": "No current repository relationship is part of this project context.",
                }
            )
        if not any(
            item.kind == "method_model" and item.state.get("accepted_current") is True
            for item in candidates
        ):
            limitations.append({"code": "current_method_model_missing"})
        if not any(
            item.kind == "mental_model" and item.state.get("accepted_current") is True
            for item in candidates
        ):
            limitations.append({"code": "current_mental_model_missing"})
        for item in selected:
            if item.state.get("review_status") == "accepted" and item.state.get(
                "freshness_status"
            ) not in {None, "current"}:
                limitations.append(
                    {
                        "code": "accepted_artifact_not_current",
                        "kind": item.kind,
                        "id": str(item.id),
                        "effective_status": item.state.get("freshness_status"),
                    }
                )
            if item.state.get("accepted_current") is True and item.state.get(
                "grounding_status"
            ) in {"unsupported", "contradicted", "mixed", "partially_grounded"}:
                limitations.append(
                    {
                        "code": "accepted_knowledge_grounding_warning",
                        "kind": item.kind,
                        "id": str(item.id),
                        "grounding_status": item.state.get("grounding_status"),
                    }
                )
            if item.provenance.get("absence_scope") == "inspection_bounded":
                limitations.append(
                    {
                        "code": "repository_absence_is_inspection_bounded",
                        "kind": item.kind,
                        "id": str(item.id),
                    }
                )
        frame = (_project_entry(project), _question_entry(question))
        selector: dict[str, object] = {
            "type": "project_context",
            "project_id": str(project.id),
            "research_question_id": str(question.id),
            "ordering": "rf-copilot-context-order-v1",
            "bounds": asdict(COPILOT_BOUNDS),
        }
        return _Selection(
            selector=selector,
            items=(*frame, *(_structured_entry(item) for item in selected)),
            limitations=tuple(sorted(limitations, key=_json_sort_key)),
            truncated=bool(omitted),
        )

    async def _select_retrieval(
        self, uow: UnitOfWork, command: BuildCopilotRetrievalCommand
    ) -> _Selection:
        await self._authorize(uow)
        intent = CopilotRetrievalIntent(command.intent)
        limitations: list[dict[str, object]] = []
        structured: tuple[CopilotStructuredItemData, ...] = ()
        evidence: tuple[CopilotEvidenceItemData, ...] = ()
        truncated = False
        if intent is CopilotRetrievalIntent.STRUCTURED_DETAIL:
            structured = await uow.copilot.get_structured_items(command.target_ids)
            _require_all_targets(command.target_ids, tuple(item.id for item in structured))
            if len(structured) > COPILOT_BOUNDS.structured_objects:
                raise DomainValidationError(details={"reason": "structured_detail_exceeds_bound"})
        elif intent is CopilotRetrievalIntent.DEPENDENCY_PROVENANCE:
            targets = await uow.copilot.get_structured_items(command.target_ids)
            _require_all_targets(command.target_ids, tuple(item.id for item in targets))
            structured = await uow.copilot.list_dependency_items(command.target_ids)
            if not structured:
                limitations.append({"code": "no_persisted_dependency_edges"})
            elif len(structured) > COPILOT_BOUNDS.structured_objects:
                ordered_dependencies = sorted(
                    structured,
                    key=lambda item: (
                        0 if item.material else 1,
                        item.kind,
                        str(item.id),
                    ),
                )
                omitted_dependencies = ordered_dependencies[COPILOT_BOUNDS.structured_objects :]
                if any(item.material for item in omitted_dependencies):
                    raise DomainValidationError(details={"reason": "material_dependency_omitted"})
                structured = tuple(ordered_dependencies[: COPILOT_BOUNDS.structured_objects])
                truncated = True
        elif intent is CopilotRetrievalIntent.STRUCTURED_SEARCH:
            structured_values = await uow.copilot.search_structured_items(
                query=(command.query or "").strip(),
                allowed_kinds=command.allowed_kinds,
                limit=COPILOT_BOUNDS.structured_objects + 1,
            )
            truncated = len(structured_values) > COPILOT_BOUNDS.structured_objects
            structured = structured_values[: COPILOT_BOUNDS.structured_objects]
        elif intent is CopilotRetrievalIntent.SOURCE_CHUNK_SEARCH:
            evidence_values = await uow.copilot.search_source_chunks(
                query=(command.query or "").strip(), limit=COPILOT_BOUNDS.source_chunks + 1
            )
            truncated = len(evidence_values) > COPILOT_BOUNDS.source_chunks
            evidence = evidence_values[: COPILOT_BOUNDS.source_chunks]
        elif intent is CopilotRetrievalIntent.REPOSITORY_COMPONENT_SEARCH:
            evidence_values = await uow.copilot.search_repository_components(
                query=(command.query or "").strip(),
                limit=COPILOT_BOUNDS.repository_components + 1,
            )
            truncated = len(evidence_values) > COPILOT_BOUNDS.repository_components
            evidence = evidence_values[: COPILOT_BOUNDS.repository_components]
        elif intent is CopilotRetrievalIntent.EXACT_EVIDENCE:
            targets = await uow.copilot.get_structured_items(command.target_ids)
            _require_all_targets(command.target_ids, tuple(item.id for item in targets))
            evidence_values = await uow.copilot.list_exact_evidence(command.target_ids)
            source = [item for item in evidence_values if item.kind == "source_chunk"]
            repository = [item for item in evidence_values if item.kind == "repository_component"]
            selected = (
                source[: COPILOT_BOUNDS.source_chunks]
                + repository[: COPILOT_BOUNDS.repository_components]
            )[: COPILOT_BOUNDS.evidence_anchors]
            omitted = [item for item in evidence_values if item not in selected]
            if any(
                item.state.get("accepted_current")
                and item.provenance.get("relation") == "contradicts"
                for item in omitted
            ):
                raise DomainValidationError(
                    details={"reason": "material_contradicting_evidence_omitted"}
                )
            evidence = tuple(selected)
            truncated = bool(omitted)
        if truncated:
            limitations.append(
                {
                    "code": "retrieval_truncated",
                    "intent": intent.value,
                    "material_omitted": False,
                }
            )
        entries: list[dict[str, object]] = [_structured_entry(item) for item in structured]
        for item in evidence:
            entry, limitation = _evidence_entry(item)
            entries.append(entry)
            if limitation is not None:
                limitations.append(limitation)
        entries.sort(key=lambda item: (str(item["kind"]), str(item["id"])))
        selector: dict[str, object] = {
            "type": "retrieval",
            "context_manifest_id": str(command.context_manifest_id),
            "intent": intent.value,
            "query": None if command.query is None else command.query.strip(),
            "target_ids": [str(value) for value in command.target_ids],
            "allowed_kinds": list(command.allowed_kinds),
            "ordering": "rf-copilot-retrieval-order-v1",
            "bounds": asdict(COPILOT_BOUNDS),
        }
        return _Selection(
            selector=selector,
            items=tuple(entries),
            limitations=tuple(sorted(limitations, key=_json_sort_key)),
            truncated=truncated,
        )

    async def _persist_revalidated(
        self,
        project_id: UUID,
        *,
        kind: CopilotManifestKind,
        parent: CopilotManifestData | None,
        policy_version: str,
        expected: _Selection,
        selector: Callable[[UnitOfWork], Awaitable[_Selection]],
    ) -> CopilotManifestData:
        async with self._uow_factory(project_id) as uow:
            await uow.configure_consistent_snapshot(read_only=False)
            await self._authorize(uow)
            current = await selector(uow)
            if _fingerprint(current) != _fingerprint(expected):
                raise ResourceConflictError(details={"reason": "copilot_context_state_changed"})
            selector_fp = _fingerprint(current.selector)
            state_fp = _fingerprint(current.items)
            manifest_fp = _fingerprint(
                {
                    "kind": kind.value,
                    "parent_manifest_fingerprint": None
                    if parent is None
                    else parent.manifest_fingerprint,
                    "selector": current.selector,
                    "items": current.items,
                    "limitations": current.limitations,
                    "truncated": current.truncated,
                    "policy_version": policy_version,
                    "bounds_policy_version": COPILOT_BOUNDS_POLICY_VERSION,
                }
            )
            existing = await uow.copilot.get_manifest_by_fingerprint(manifest_fp)
            if existing is not None:
                await uow.rollback()
                return existing
            manifest = await uow.copilot.add_manifest(
                NewCopilotManifest(
                    id=uuid5(_NAMESPACE, f"manifest:{project_id}:{manifest_fp}"),
                    project_id=project_id,
                    kind=kind.value,
                    parent_manifest_id=None if parent is None else parent.id,
                    selector=current.selector,
                    selector_fingerprint=selector_fp,
                    state_fingerprint=state_fp,
                    policy_version=policy_version,
                    bounds_policy_version=COPILOT_BOUNDS_POLICY_VERSION,
                    ordered_items=current.items,
                    limitations=current.limitations,
                    truncated=current.truncated,
                    manifest_fingerprint=manifest_fp,
                )
            )
            await uow.commit()
            return manifest

    async def _authorize(self, uow: UnitOfWork) -> tuple[ProjectData, ResearchQuestionData]:
        project = await uow.projects.get()
        if project is None or project.created_by_id != self._actor.actor_id:
            raise ResourceNotFoundError(details={"resource": "project"})
        question = await uow.research_questions.get_current_primary()
        if question is None or question.id != project.primary_research_question_id:
            raise ResourceConflictError(details={"reason": "current_research_question_missing"})
        return project, question


def _project_entry(project: ProjectData) -> dict[str, object]:
    return {
        "kind": "project",
        "id": str(project.id),
        "identity": {"id": str(project.id), "lock_version": project.lock_version},
        "content": {
            "title": project.title,
            "objective": project.description,
            "target_outcome": project.target_outcome,
            "research_depth": project.reproduction_depth,
        },
        "state": {"canonical": True},
        "provenance": {},
    }


def _question_entry(question: ResearchQuestionData) -> dict[str, object]:
    return {
        "kind": "research_question",
        "id": str(question.id),
        "identity": {
            "id": str(question.id),
            "lineage_id": str(question.lineage_id),
            "version": question.version,
        },
        "content": {"question": question.question_text, "scope": question.scope_text},
        "state": {
            "inquiry_status": question.inquiry_status,
            "lifecycle_status": question.lifecycle_status,
            "canonical": question.lifecycle_status == "current",
        },
        "provenance": {},
    }


def _structured_entry(item: CopilotStructuredItemData) -> dict[str, object]:
    return {
        "kind": item.kind,
        "id": str(item.id),
        "identity": _jsonable(item.identity),
        "content": item.content,
        "state": _jsonable(item.state),
        "provenance": _jsonable(item.provenance),
        "material": item.material,
        "content_fingerprint": _fingerprint(item.content),
    }


def _evidence_entry(
    item: CopilotEvidenceItemData,
) -> tuple[dict[str, object], dict[str, object] | None]:
    content = item.content
    limitation = None
    if len(content) > COPILOT_BOUNDS.excerpt_codepoints:
        content = content[: COPILOT_BOUNDS.excerpt_codepoints]
        limitation = {
            "code": "evidence_excerpt_truncated",
            "kind": item.kind,
            "id": str(item.id),
            "full_content_hash": item.content_hash,
            "included_codepoints": COPILOT_BOUNDS.excerpt_codepoints,
        }
    return (
        {
            "kind": item.kind,
            "id": str(item.id),
            "identity": _jsonable(item.identity),
            "content": content,
            "state": _jsonable(item.state),
            "provenance": _jsonable(item.provenance),
            "full_content_hash": item.content_hash,
            "included_content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "untrusted_evidence": True,
        },
        limitation,
    )


def _context_order(item: CopilotStructuredItemData) -> tuple[object, ...]:
    state = item.state
    return (
        0 if item.material else 1,
        0 if state.get("accepted_current") is True else 1,
        item.kind,
        str(item.id),
    )


def _require_all_targets(requested: tuple[UUID, ...], found: tuple[UUID, ...]) -> None:
    missing = set(requested) - set(found)
    if missing:
        raise ResourceNotFoundError(details={"resource": "copilot_retrieval_target"})


def _jsonable(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _fingerprint(value: object) -> str:
    raw = json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _json_sort_key(value: dict[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
