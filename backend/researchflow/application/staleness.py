"""Application boundary for deterministic, direct-only artifact invalidation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

from researchflow.domain.actor import ActorContext
from researchflow.domain.staleness import (
    DIRECT_STALENESS_POLICY_VERSION,
    INVALIDATABLE_ARTIFACT_TYPES,
    reason_text,
    rule_for,
    strongest_effect,
)
from researchflow.errors import ResourceConflictError, ResourceNotFoundError
from researchflow.ports.repositories import (
    ArtifactInvalidationData,
    NewOperationEvent,
)
from researchflow.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory

_NAMESPACE = uuid5(NAMESPACE_URL, "https://researchflow.local/")


@dataclass(frozen=True, slots=True)
class DirectStalenessCommand:
    project_id: UUID
    cause_type: str
    cause_id: UUID
    operation_type: str
    after_id: UUID | None = None
    expected_preview_fingerprint: str | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class DirectStalenessEffect:
    dependency_id: UUID
    artifact_type: str
    artifact_id: UUID
    effect: str
    reason_code: str
    reason: str


@dataclass(frozen=True, slots=True)
class DirectStalenessPreview:
    policy_version: str
    scope: str
    complete: bool
    warning: str
    cause_type: str
    cause_id: UUID
    operation_type: str
    effects: tuple[DirectStalenessEffect, ...]
    fingerprint: str


@dataclass(frozen=True, slots=True)
class ArtifactFreshness:
    artifact_type: str
    artifact_id: UUID
    effective_status: str
    invalidations: tuple[ArtifactInvalidationData, ...]


@dataclass(frozen=True, slots=True)
class ResolveInvalidationCommand:
    project_id: UUID
    artifact_type: str
    artifact_id: UUID
    invalidation_id: UUID
    rationale: str
    request_id: str | None = None


class StalenessService:
    def __init__(self, *, actor: ActorContext, uow_factory: UnitOfWorkFactory) -> None:
        self._actor = actor
        self._uow_factory = uow_factory

    async def preview_direct(self, command: DirectStalenessCommand) -> DirectStalenessPreview:
        async with self._uow_factory(command.project_id) as uow:
            await self._require_owned(uow)
            preview = await self._preview_in_uow(uow, command)
            await uow.rollback()
            return preview

    async def apply_direct(self, command: DirectStalenessCommand) -> DirectStalenessPreview:
        async with self._uow_factory(command.project_id) as uow:
            await self._require_owned(uow)
            preview = await self.apply_direct_in_uow(uow, command)
            await uow.commit()
            return preview

    async def apply_direct_in_uow(
        self, uow: UnitOfWork, command: DirectStalenessCommand
    ) -> DirectStalenessPreview:
        if uow.project_id != command.project_id:
            raise ResourceConflictError(details={"reason": "project_scope_mismatch"})
        preview = await self._preview_in_uow(uow, command)
        if (
            command.expected_preview_fingerprint is not None
            and command.expected_preview_fingerprint != preview.fingerprint
        ):
            raise ResourceConflictError(details={"reason": "stale_staleness_preview"})
        for effect in preview.effects:
            if await uow.dependencies.artifact_is_superseded(
                effect.artifact_type, effect.artifact_id
            ):
                raise ResourceConflictError(details={"reason": "dependent_artifact_superseded"})
            invalidation = ArtifactInvalidationData(
                id=_id(
                    "invalidation",
                    effect.artifact_type,
                    effect.artifact_id,
                    command.cause_type,
                    command.cause_id,
                    effect.reason_code,
                ),
                project_id=command.project_id,
                artifact_type=effect.artifact_type,
                artifact_id=effect.artifact_id,
                cause_type=command.cause_type,
                cause_id=command.cause_id,
                rule_code=effect.reason_code,
                effect=effect.effect,
                reason=effect.reason,
            )
            persisted = await uow.dependencies.append_invalidation(invalidation)
            unresolved = await uow.dependencies.list_unresolved_invalidations(
                effect.artifact_type, effect.artifact_id
            )
            await uow.dependencies.set_freshness_projection(
                effect.artifact_type,
                effect.artifact_id,
                strongest_effect(tuple(item.effect for item in unresolved)),
            )
            await uow.events.append(
                NewOperationEvent(
                    id=_id("event", persisted.id),
                    project_id=command.project_id,
                    workflow_run_id=None,
                    operation_type="artifact.directly_invalidated",
                    created_by_type="system",
                    created_by_id=None,
                    input_refs=(
                        {"type": command.cause_type, "id": str(command.cause_id)},
                        {"type": "artifact_dependency", "id": str(effect.dependency_id)},
                    ),
                    output_refs=(
                        {"type": "artifact_invalidation", "id": str(persisted.id)},
                        {"type": effect.artifact_type, "id": str(effect.artifact_id)},
                    ),
                    reason=effect.reason,
                    idempotency_key=f"direct-invalidation:{persisted.id}",
                    input_fingerprint=preview.fingerprint,
                    request_id=command.request_id,
                )
            )
        return preview

    async def artifact_changed(
        self,
        *,
        uow: UnitOfWork,
        project_id: UUID,
        cause_type: str,
        before_id: UUID,
        after_id: UUID | None,
        operation_type: str,
    ) -> None:
        await self.apply_direct_in_uow(
            uow,
            DirectStalenessCommand(
                project_id=project_id,
                cause_type=cause_type,
                cause_id=before_id,
                after_id=after_id,
                operation_type=operation_type,
            ),
        )

    async def current(
        self, project_id: UUID, artifact_type: str, artifact_id: UUID
    ) -> ArtifactFreshness:
        async with self._uow_factory(project_id) as uow:
            await self._require_owned(uow)
            await uow.dependencies.artifact_is_superseded(artifact_type, artifact_id)
            values = await uow.dependencies.list_unresolved_invalidations(
                artifact_type, artifact_id
            )
            await uow.rollback()
            return ArtifactFreshness(
                artifact_type=artifact_type,
                artifact_id=artifact_id,
                effective_status=strongest_effect(tuple(item.effect for item in values)),
                invalidations=values,
            )

    async def resolve(self, command: ResolveInvalidationCommand) -> ArtifactFreshness:
        rationale = command.rationale.strip()
        if not rationale or len(rationale) > 4_000:
            raise ResourceConflictError(details={"reason": "resolution_rationale_invalid"})
        async with self._uow_factory(command.project_id) as uow:
            await self._require_owned(uow)
            resolution_identity = {
                "policy_version": DIRECT_STALENESS_POLICY_VERSION,
                "artifact_type": command.artifact_type,
                "artifact_id": str(command.artifact_id),
                "invalidation_id": str(command.invalidation_id),
                "rationale": rationale,
            }
            resolution_fingerprint = hashlib.sha256(
                json.dumps(resolution_identity, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            event_key = f"resolve-invalidation:{command.invalidation_id}"
            replay = await uow.events.get_by_idempotency_key(
                "artifact.invalidation_resolved", event_key
            )
            if replay is not None:
                if replay.input_fingerprint != resolution_fingerprint:
                    raise ResourceConflictError(
                        details={"reason": "idempotency_fingerprint_mismatch"}
                    )
                remaining = await uow.dependencies.list_unresolved_invalidations(
                    command.artifact_type, command.artifact_id
                )
                status = strongest_effect(tuple(item.effect for item in remaining))
                await uow.rollback()
                return ArtifactFreshness(
                    command.artifact_type, command.artifact_id, status, remaining
                )
            values = await uow.dependencies.list_unresolved_invalidations(
                command.artifact_type, command.artifact_id
            )
            target = next((item for item in values if item.id == command.invalidation_id), None)
            if target is None:
                raise ResourceNotFoundError(details={"resource": "artifact_invalidation"})
            await uow.dependencies.resolve_invalidation(target.id)
            remaining = await uow.dependencies.list_unresolved_invalidations(
                command.artifact_type, command.artifact_id
            )
            status = strongest_effect(tuple(item.effect for item in remaining))
            await uow.dependencies.set_freshness_projection(
                command.artifact_type, command.artifact_id, status
            )
            await uow.events.append(
                NewOperationEvent(
                    id=_id("resolution", target.id),
                    project_id=command.project_id,
                    workflow_run_id=None,
                    operation_type="artifact.invalidation_resolved",
                    created_by_type="human",
                    created_by_id=self._actor.actor_id,
                    input_refs=({"type": "artifact_invalidation", "id": str(target.id)},),
                    output_refs=({"type": command.artifact_type, "id": str(command.artifact_id)},),
                    reason=rationale,
                    idempotency_key=event_key,
                    input_fingerprint=resolution_fingerprint,
                    request_id=command.request_id,
                )
            )
            await uow.commit()
            return ArtifactFreshness(command.artifact_type, command.artifact_id, status, remaining)

    async def _preview_in_uow(
        self, uow: UnitOfWork, command: DirectStalenessCommand
    ) -> DirectStalenessPreview:
        rule = rule_for(command.operation_type)
        effects: tuple[DirectStalenessEffect, ...] = ()
        if rule is not None:
            if rule.cause_type != command.cause_type:
                raise ResourceConflictError(details={"reason": "staleness_cause_type_mismatch"})
            dependencies = await uow.dependencies.list_dependents(
                command.cause_type, command.cause_id
            )
            mutable_effects: list[DirectStalenessEffect] = []
            for item in dependencies:
                if (
                    item.artifact_type not in INVALIDATABLE_ARTIFACT_TYPES
                    or (rule.material_only and item.criticality == "informational")
                    or item.artifact_id == command.after_id
                    or await uow.dependencies.artifact_is_superseded(
                        item.artifact_type, item.artifact_id
                    )
                ):
                    continue
                mutable_effects.append(
                    DirectStalenessEffect(
                        dependency_id=item.id,
                        artifact_type=item.artifact_type,
                        artifact_id=item.artifact_id,
                        effect=rule.effect.value,
                        reason_code=rule.reason.value,
                        reason=reason_text(rule.reason),
                    )
                )
            effects = tuple(mutable_effects)
        identity = {
            "policy_version": DIRECT_STALENESS_POLICY_VERSION,
            "scope": "direct",
            "cause_type": command.cause_type,
            "cause_id": str(command.cause_id),
            "after_id": None if command.after_id is None else str(command.after_id),
            "operation_type": command.operation_type,
            "effects": [
                {
                    "dependency_id": str(item.dependency_id),
                    "artifact_type": item.artifact_type,
                    "artifact_id": str(item.artifact_id),
                    "effect": item.effect,
                    "reason_code": item.reason_code,
                }
                for item in effects
            ],
        }
        fingerprint = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return DirectStalenessPreview(
            policy_version=DIRECT_STALENESS_POLICY_VERSION,
            scope="direct",
            complete=False,
            warning=(
                "This bounded preview includes direct persisted dependents only; additional "
                "downstream effects may appear during later normal propagation."
            ),
            cause_type=command.cause_type,
            cause_id=command.cause_id,
            operation_type=command.operation_type,
            effects=effects,
            fingerprint=fingerprint,
        )

    async def _require_owned(self, uow: UnitOfWork) -> None:
        project = await uow.projects.get()
        if project is None or project.created_by_id != self._actor.actor_id:
            raise ResourceNotFoundError(details={"resource": "project"})


def _id(*parts: object) -> UUID:
    return uuid5(_NAMESPACE, ":".join(str(part) for part in parts))
