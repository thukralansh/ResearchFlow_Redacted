"""Repository identity, snapshot, and paper-repository relationship queries."""

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from researchflow.errors import ResourceConflictError
from researchflow.infrastructure.database.models.repository import (
    PaperRepositoryRelationshipRecord,
    RepositoryComponentRecord,
    RepositoryDiscoveryRunRecord,
    RepositoryInspectionRunRecord,
    RepositoryRecord,
    RepositorySnapshotRecord,
    RepositoryTreeEntryRecord,
)
from researchflow.infrastructure.database.repositories._errors import execute_database
from researchflow.ports.repositories import (
    NewPaperRepositoryRelationship,
    NewRepository,
    NewRepositoryComponent,
    NewRepositoryDiscoveryRun,
    NewRepositoryInspectionComponent,
    NewRepositoryInspectionRun,
    NewRepositorySnapshot,
    NewRepositoryTreeEntry,
    PaperRepositoryRelationshipData,
    RepositoryComponentData,
    RepositoryData,
    RepositoryDiscoveryRunData,
    RepositoryInspectionRunData,
    RepositorySnapshotData,
    RepositoryTreeEntryData,
)


def _to_repository(row: RepositoryRecord) -> RepositoryData:
    return RepositoryData(
        id=row.id,
        project_id=row.project_id,
        owner=row.owner,
        name=row.name,
        normalized_url=row.normalized_url,
        default_branch=row.default_branch,
        description=row.description,
        is_private=row.is_private,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_snapshot(row: RepositorySnapshotRecord) -> RepositorySnapshotData:
    return RepositorySnapshotData(
        id=row.id,
        repository_id=row.repository_id,
        requested_ref=row.requested_ref,
        commit_sha=row.commit_sha,
        status=row.status,
        lifecycle_status=row.lifecycle_status,
        supersedes_id=row.supersedes_id,
        fetched_at=row.fetched_at,
    )


def _to_relationship(
    row: PaperRepositoryRelationshipRecord,
) -> PaperRepositoryRelationshipData:
    return PaperRepositoryRelationshipData(
        id=row.id,
        project_id=row.project_id,
        source_id=row.source_id,
        repository_id=row.repository_id,
        repository_snapshot_id=row.repository_snapshot_id,
        lineage_id=row.lineage_id,
        version=row.version,
        relationship_type=row.relationship_type,
        authority_level=row.authority_level,
        rationale=row.rationale,
        evidence_text=row.evidence_text,
        confidence=row.confidence,
        review_status=row.review_status,
        lifecycle_status=row.lifecycle_status,
        created_by_type=row.created_by_type,
        derived_from_id=row.derived_from_id,
        supersedes_id=row.supersedes_id,
        created_at=row.created_at,
    )


def _to_discovery_run(row: RepositoryDiscoveryRunRecord) -> RepositoryDiscoveryRunData:
    return RepositoryDiscoveryRunData(
        id=row.id,
        repository_snapshot_id=row.repository_snapshot_id,
        discovery_version=row.discovery_version,
        tree_hash=row.tree_hash,
        configured_limits=dict(row.configured_limits),
        coverage_status=row.coverage_status,
        status=row.status,
        error_code=row.error_code,
        error_detail=row.error_detail,
        created_at=row.created_at,
    )


def _to_tree_entry(row: RepositoryTreeEntryRecord) -> RepositoryTreeEntryData:
    return RepositoryTreeEntryData(
        id=row.id,
        discovery_run_id=row.discovery_run_id,
        path=row.path,
        entry_type=row.entry_type,
        language=row.language,
        coarse_role=row.coarse_role,
        size_bytes=row.size_bytes,
        excluded_reason=row.excluded_reason,
    )


def _to_component(row: RepositoryComponentRecord) -> RepositoryComponentData:
    return RepositoryComponentData(
        id=row.id,
        repository_snapshot_id=row.repository_snapshot_id,
        analysis_stage=row.analysis_stage,
        repository_discovery_run_id=row.repository_discovery_run_id,
        repository_inspection_run_id=row.repository_inspection_run_id,
        path=row.path,
        component_type=row.component_type,
        language=row.language,
        symbol=row.symbol,
        line_start=row.line_start,
        line_end=row.line_end,
        content_excerpt=row.content_excerpt,
        content_hash=row.content_hash,
        relevance_score=row.relevance_score,
    )


def _to_inspection_run(row: RepositoryInspectionRunRecord) -> RepositoryInspectionRunData:
    return RepositoryInspectionRunData(
        id=row.id,
        repository_snapshot_id=row.repository_snapshot_id,
        discovery_run_id=row.discovery_run_id,
        guidance_fingerprint=row.guidance_fingerprint,
        ranking_version=row.ranking_version,
        analysis_version=row.analysis_version,
        configured_limits=row.configured_limits,
        selected_paths=tuple(row.selected_paths),
        inspected_paths=tuple(row.inspected_paths),
        coverage_status=row.coverage_status,
        status=row.status,
        error_code=row.error_code,
        error_detail=row.error_detail,
        created_at=row.created_at,
    )


class SqlAlchemyRepositoriesRepository:
    def __init__(self, session: AsyncSession, project_id: UUID) -> None:
        self._session = session
        self._project_id = project_id

    @property
    def project_id(self) -> UUID:
        return self._project_id

    async def add(self, repository: NewRepository) -> RepositoryData:
        if repository.project_id != self._project_id:
            raise ResourceConflictError(details={"reason": "project_scope_mismatch"})
        existing = await self.get_exact(repository.id)
        if existing is not None:
            expected = (
                repository.owner,
                repository.name,
                repository.normalized_url,
                repository.is_private,
            )
            actual = (
                existing.owner,
                existing.name,
                existing.normalized_url,
                existing.is_private,
            )
            if actual == expected:
                return existing
            raise ResourceConflictError(details={"reason": "repository_id_reused"})
        by_identity = await self.get_by_identity(repository.owner, repository.name)
        if by_identity is not None:
            return by_identity
        row = RepositoryRecord(
            id=repository.id,
            project_id=repository.project_id,
            host="github.com",
            owner=repository.owner,
            name=repository.name,
            normalized_url=repository.normalized_url,
            default_branch=repository.default_branch,
            description=repository.description,
            is_private=repository.is_private,
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_repository(row)

    async def get_exact(self, repository_id: UUID) -> RepositoryData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(RepositoryRecord).where(
                    RepositoryRecord.id == repository_id,
                    RepositoryRecord.project_id == self._project_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_repository(row)

    async def get_by_identity(self, owner: str, name: str) -> RepositoryData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(RepositoryRecord).where(
                    RepositoryRecord.project_id == self._project_id,
                    RepositoryRecord.owner == owner,
                    RepositoryRecord.name == name,
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_repository(row)

    async def add_snapshot(self, snapshot: NewRepositorySnapshot) -> RepositorySnapshotData:
        if await self.get_exact(snapshot.repository_id) is None:
            raise ResourceConflictError(details={"reason": "repository_project_scope_mismatch"})
        existing = await self.get_snapshot_by_commit(snapshot.repository_id, snapshot.commit_sha)
        if existing is not None:
            return existing
        row = RepositorySnapshotRecord(
            id=snapshot.id,
            repository_id=snapshot.repository_id,
            requested_ref=snapshot.requested_ref,
            commit_sha=snapshot.commit_sha,
            status=snapshot.status,
            lifecycle_status=snapshot.lifecycle_status,
            supersedes_id=snapshot.supersedes_id,
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_snapshot(row)

    async def get_current_snapshot(self, repository_id: UUID) -> RepositorySnapshotData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(RepositorySnapshotRecord)
                .join(
                    RepositoryRecord,
                    RepositoryRecord.id == RepositorySnapshotRecord.repository_id,
                )
                .where(
                    RepositorySnapshotRecord.repository_id == repository_id,
                    RepositorySnapshotRecord.lifecycle_status == "current",
                    RepositoryRecord.project_id == self._project_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_snapshot(row)

    async def get_snapshot_exact(self, snapshot_id: UUID) -> RepositorySnapshotData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(RepositorySnapshotRecord)
                .join(
                    RepositoryRecord,
                    RepositoryRecord.id == RepositorySnapshotRecord.repository_id,
                )
                .where(
                    RepositorySnapshotRecord.id == snapshot_id,
                    RepositoryRecord.project_id == self._project_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_snapshot(row)

    async def get_snapshot_by_commit(
        self, repository_id: UUID, commit_sha: str
    ) -> RepositorySnapshotData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(RepositorySnapshotRecord)
                .join(
                    RepositoryRecord,
                    RepositoryRecord.id == RepositorySnapshotRecord.repository_id,
                )
                .where(
                    RepositorySnapshotRecord.repository_id == repository_id,
                    RepositorySnapshotRecord.commit_sha == commit_sha,
                    RepositoryRecord.project_id == self._project_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_snapshot(row)

    async def mark_snapshot_superseded(self, snapshot_id: UUID) -> RepositorySnapshotData:
        result = await execute_database(
            lambda: self._session.execute(
                update(RepositorySnapshotRecord)
                .where(
                    RepositorySnapshotRecord.id == snapshot_id,
                    RepositorySnapshotRecord.repository_id.in_(
                        select(RepositoryRecord.id).where(
                            RepositoryRecord.project_id == self._project_id
                        )
                    ),
                    RepositorySnapshotRecord.lifecycle_status == "current",
                )
                .values(lifecycle_status="superseded")
                .returning(RepositorySnapshotRecord)
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise ResourceConflictError(details={"reason": "stale_repository_snapshot"})
        return _to_snapshot(row)

    async def mark_snapshot_current(self, snapshot_id: UUID) -> RepositorySnapshotData:
        result = await execute_database(
            lambda: self._session.execute(
                update(RepositorySnapshotRecord)
                .where(
                    RepositorySnapshotRecord.id == snapshot_id,
                    RepositorySnapshotRecord.repository_id.in_(
                        select(RepositoryRecord.id).where(
                            RepositoryRecord.project_id == self._project_id
                        )
                    ),
                    RepositorySnapshotRecord.lifecycle_status == "superseded",
                )
                .values(lifecycle_status="current")
                .returning(RepositorySnapshotRecord)
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise ResourceConflictError(details={"reason": "stale_repository_snapshot"})
        return _to_snapshot(row)

    async def add_relationship(
        self, relationship: NewPaperRepositoryRelationship
    ) -> PaperRepositoryRelationshipData:
        if relationship.project_id != self._project_id:
            raise ResourceConflictError(details={"reason": "project_scope_mismatch"})
        existing = await self.get_relationship_exact(relationship.id)
        if existing is not None:
            return existing
        row = PaperRepositoryRelationshipRecord(
            id=relationship.id,
            project_id=relationship.project_id,
            source_id=relationship.source_id,
            repository_id=relationship.repository_id,
            repository_snapshot_id=relationship.repository_snapshot_id,
            lineage_id=relationship.lineage_id,
            version=relationship.version,
            relationship_type=relationship.relationship_type,
            authority_level=relationship.authority_level,
            rationale=relationship.rationale,
            evidence_text=relationship.evidence_text,
            confidence=relationship.confidence,
            review_status=relationship.review_status,
            lifecycle_status=relationship.lifecycle_status,
            created_by_type=relationship.created_by_type,
            derived_from_id=relationship.derived_from_id,
            supersedes_id=relationship.supersedes_id,
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_relationship(row)

    async def get_accepted_current_relationship(
        self,
    ) -> PaperRepositoryRelationshipData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(PaperRepositoryRelationshipRecord).where(
                    PaperRepositoryRelationshipRecord.project_id == self._project_id,
                    PaperRepositoryRelationshipRecord.review_status == "accepted",
                    PaperRepositoryRelationshipRecord.lifecycle_status == "current",
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_relationship(row)

    async def get_relationship_exact(
        self, relationship_id: UUID
    ) -> PaperRepositoryRelationshipData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(PaperRepositoryRelationshipRecord).where(
                    PaperRepositoryRelationshipRecord.id == relationship_id,
                    PaperRepositoryRelationshipRecord.project_id == self._project_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_relationship(row)

    async def list_relationship_history(
        self,
    ) -> tuple[PaperRepositoryRelationshipData, ...]:
        result = await execute_database(
            lambda: self._session.execute(
                select(PaperRepositoryRelationshipRecord)
                .where(PaperRepositoryRelationshipRecord.project_id == self._project_id)
                .order_by(
                    PaperRepositoryRelationshipRecord.lineage_id,
                    PaperRepositoryRelationshipRecord.version,
                )
            )
        )
        return tuple(_to_relationship(row) for row in result.scalars())

    async def mark_relationship_superseded(
        self, relationship_id: UUID, *, expected_version: int
    ) -> PaperRepositoryRelationshipData:
        result = await execute_database(
            lambda: self._session.execute(
                update(PaperRepositoryRelationshipRecord)
                .where(
                    PaperRepositoryRelationshipRecord.id == relationship_id,
                    PaperRepositoryRelationshipRecord.project_id == self._project_id,
                    PaperRepositoryRelationshipRecord.version == expected_version,
                    PaperRepositoryRelationshipRecord.lifecycle_status == "current",
                )
                .values(lifecycle_status="superseded")
                .returning(PaperRepositoryRelationshipRecord)
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise ResourceConflictError(details={"reason": "stale_paper_repository_relationship"})
        return _to_relationship(row)

    async def add_discovery_run(self, run: NewRepositoryDiscoveryRun) -> RepositoryDiscoveryRunData:
        if await self.get_snapshot_exact(run.repository_snapshot_id) is None:
            raise ResourceConflictError(details={"reason": "repository_snapshot_project_scope"})
        row = RepositoryDiscoveryRunRecord(
            id=run.id,
            repository_snapshot_id=run.repository_snapshot_id,
            discovery_version=run.discovery_version,
            tree_hash=None,
            configured_limits=run.configured_limits,
            coverage_status=run.coverage_status,
            status=run.status,
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_discovery_run(row)

    async def get_discovery_run_exact(self, run_id: UUID) -> RepositoryDiscoveryRunData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(RepositoryDiscoveryRunRecord)
                .join(
                    RepositorySnapshotRecord,
                    RepositorySnapshotRecord.id
                    == RepositoryDiscoveryRunRecord.repository_snapshot_id,
                )
                .join(
                    RepositoryRecord,
                    RepositoryRecord.id == RepositorySnapshotRecord.repository_id,
                )
                .where(
                    RepositoryDiscoveryRunRecord.id == run_id,
                    RepositoryRecord.project_id == self._project_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_discovery_run(row)

    async def get_successful_discovery_run(
        self,
        *,
        repository_snapshot_id: UUID,
        discovery_version: str,
        limit_fingerprint: str,
    ) -> RepositoryDiscoveryRunData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(RepositoryDiscoveryRunRecord)
                .join(
                    RepositorySnapshotRecord,
                    RepositorySnapshotRecord.id
                    == RepositoryDiscoveryRunRecord.repository_snapshot_id,
                )
                .join(
                    RepositoryRecord,
                    RepositoryRecord.id == RepositorySnapshotRecord.repository_id,
                )
                .where(
                    RepositoryDiscoveryRunRecord.repository_snapshot_id == repository_snapshot_id,
                    RepositoryDiscoveryRunRecord.discovery_version == discovery_version,
                    RepositoryDiscoveryRunRecord.status == "succeeded",
                    RepositoryDiscoveryRunRecord.configured_limits.op("->>")("limit_fingerprint")
                    == limit_fingerprint,
                    RepositoryRecord.project_id == self._project_id,
                )
                .order_by(RepositoryDiscoveryRunRecord.created_at.desc())
                .limit(1)
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_discovery_run(row)

    async def list_discovery_runs(
        self, repository_snapshot_id: UUID | None = None
    ) -> tuple[RepositoryDiscoveryRunData, ...]:
        statement = (
            select(RepositoryDiscoveryRunRecord)
            .join(
                RepositorySnapshotRecord,
                RepositorySnapshotRecord.id == RepositoryDiscoveryRunRecord.repository_snapshot_id,
            )
            .join(
                RepositoryRecord,
                RepositoryRecord.id == RepositorySnapshotRecord.repository_id,
            )
            .where(RepositoryRecord.project_id == self._project_id)
            .order_by(RepositoryDiscoveryRunRecord.created_at, RepositoryDiscoveryRunRecord.id)
        )
        if repository_snapshot_id is not None:
            statement = statement.where(
                RepositoryDiscoveryRunRecord.repository_snapshot_id == repository_snapshot_id
            )
        result = await execute_database(lambda: self._session.execute(statement))
        return tuple(_to_discovery_run(row) for row in result.scalars())

    async def finish_discovery_run(
        self,
        run_id: UUID,
        *,
        expected_status: str,
        status: str,
        coverage_status: str,
        configured_limits: dict[str, object],
        tree_hash: str | None,
        error_code: str | None = None,
        error_detail: str | None = None,
    ) -> RepositoryDiscoveryRunData:
        result = await execute_database(
            lambda: self._session.execute(
                update(RepositoryDiscoveryRunRecord)
                .where(
                    RepositoryDiscoveryRunRecord.id == run_id,
                    RepositoryDiscoveryRunRecord.status == expected_status,
                    RepositoryDiscoveryRunRecord.repository_snapshot_id.in_(
                        select(RepositorySnapshotRecord.id)
                        .join(RepositoryRecord)
                        .where(RepositoryRecord.project_id == self._project_id)
                    ),
                )
                .values(
                    status=status,
                    coverage_status=coverage_status,
                    configured_limits=configured_limits,
                    tree_hash=tree_hash,
                    error_code=error_code,
                    error_detail=error_detail,
                )
                .returning(RepositoryDiscoveryRunRecord)
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise ResourceConflictError(details={"reason": "stale_repository_discovery_run"})
        return _to_discovery_run(row)

    async def add_tree_entries(
        self, entries: tuple[NewRepositoryTreeEntry, ...]
    ) -> tuple[RepositoryTreeEntryData, ...]:
        if not entries:
            return ()
        run_id = entries[0].discovery_run_id
        if any(entry.discovery_run_id != run_id for entry in entries):
            raise ResourceConflictError(details={"reason": "mixed_repository_discovery_runs"})
        if await self.get_discovery_run_exact(run_id) is None:
            raise ResourceConflictError(details={"reason": "repository_discovery_project_scope"})
        rows = [
            RepositoryTreeEntryRecord(
                id=entry.id,
                discovery_run_id=entry.discovery_run_id,
                path=entry.path,
                entry_type=entry.entry_type,
                language=entry.language,
                coarse_role=entry.coarse_role,
                size_bytes=entry.size_bytes,
                excluded_reason=entry.excluded_reason,
            )
            for entry in entries
        ]
        self._session.add_all(rows)
        await execute_database(self._session.flush)
        return tuple(_to_tree_entry(row) for row in rows)

    async def list_tree_entries(
        self, discovery_run_id: UUID
    ) -> tuple[RepositoryTreeEntryData, ...]:
        if await self.get_discovery_run_exact(discovery_run_id) is None:
            return ()
        result = await execute_database(
            lambda: self._session.execute(
                select(RepositoryTreeEntryRecord)
                .where(RepositoryTreeEntryRecord.discovery_run_id == discovery_run_id)
                .order_by(
                    func.lower(RepositoryTreeEntryRecord.path), RepositoryTreeEntryRecord.path
                )
            )
        )
        return tuple(_to_tree_entry(row) for row in result.scalars())

    async def add_components(
        self, components: tuple[NewRepositoryComponent, ...]
    ) -> tuple[RepositoryComponentData, ...]:
        if not components:
            return ()
        run_id = components[0].repository_discovery_run_id
        run = await self.get_discovery_run_exact(run_id)
        if run is None or any(
            component.repository_discovery_run_id != run_id
            or component.repository_snapshot_id != run.repository_snapshot_id
            for component in components
        ):
            raise ResourceConflictError(details={"reason": "repository_component_scope"})
        rows = [
            RepositoryComponentRecord(
                id=component.id,
                repository_snapshot_id=component.repository_snapshot_id,
                analysis_stage="stage_a",
                repository_discovery_run_id=component.repository_discovery_run_id,
                repository_inspection_run_id=None,
                path=component.path,
                component_type=component.component_type,
                language=component.language,
                symbol=None,
                line_start=component.line_start,
                line_end=component.line_end,
                content_excerpt=component.content_excerpt,
                content_hash=component.content_hash,
                relevance_score=None,
            )
            for component in components
        ]
        self._session.add_all(rows)
        await execute_database(self._session.flush)
        return tuple(_to_component(row) for row in rows)

    async def list_discovery_components(
        self, discovery_run_id: UUID
    ) -> tuple[RepositoryComponentData, ...]:
        if await self.get_discovery_run_exact(discovery_run_id) is None:
            return ()
        result = await execute_database(
            lambda: self._session.execute(
                select(RepositoryComponentRecord)
                .where(
                    RepositoryComponentRecord.repository_discovery_run_id == discovery_run_id,
                    RepositoryComponentRecord.analysis_stage == "stage_a",
                )
                .order_by(
                    func.lower(RepositoryComponentRecord.path), RepositoryComponentRecord.path
                )
            )
        )
        return tuple(_to_component(row) for row in result.scalars())

    async def add_inspection_run(
        self, run: NewRepositoryInspectionRun
    ) -> RepositoryInspectionRunData:
        discovery = await self.get_discovery_run_exact(run.discovery_run_id)
        if discovery is None or discovery.repository_snapshot_id != run.repository_snapshot_id:
            raise ResourceConflictError(details={"reason": "repository_inspection_scope"})
        row = RepositoryInspectionRunRecord(
            id=run.id,
            repository_snapshot_id=run.repository_snapshot_id,
            discovery_run_id=run.discovery_run_id,
            guidance_fingerprint=run.guidance_fingerprint,
            ranking_version=run.ranking_version,
            analysis_version=run.analysis_version,
            configured_limits=run.configured_limits,
            selected_paths=list(run.selected_paths),
            inspected_paths=list(run.inspected_paths),
            coverage_status=run.coverage_status,
            status=run.status,
            error_code=run.error_code,
            error_detail=run.error_detail,
        )
        self._session.add(row)
        await execute_database(self._session.flush)
        return _to_inspection_run(row)

    async def get_inspection_run_exact(self, run_id: UUID) -> RepositoryInspectionRunData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(RepositoryInspectionRunRecord)
                .join(
                    RepositorySnapshotRecord,
                    RepositorySnapshotRecord.id
                    == RepositoryInspectionRunRecord.repository_snapshot_id,
                )
                .join(RepositoryRecord)
                .where(
                    RepositoryInspectionRunRecord.id == run_id,
                    RepositoryRecord.project_id == self._project_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_inspection_run(row)

    async def get_successful_inspection_run(
        self,
        *,
        repository_snapshot_id: UUID,
        discovery_run_id: UUID,
        guidance_fingerprint: str,
        ranking_version: str,
        analysis_version: str,
    ) -> RepositoryInspectionRunData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(RepositoryInspectionRunRecord)
                .join(RepositorySnapshotRecord)
                .join(RepositoryRecord)
                .where(
                    RepositoryInspectionRunRecord.repository_snapshot_id == repository_snapshot_id,
                    RepositoryInspectionRunRecord.discovery_run_id == discovery_run_id,
                    RepositoryInspectionRunRecord.guidance_fingerprint == guidance_fingerprint,
                    RepositoryInspectionRunRecord.ranking_version == ranking_version,
                    RepositoryInspectionRunRecord.analysis_version == analysis_version,
                    RepositoryInspectionRunRecord.status == "succeeded",
                    RepositoryRecord.project_id == self._project_id,
                )
                .order_by(RepositoryInspectionRunRecord.created_at.desc())
                .limit(1)
            )
        )
        row = result.scalar_one_or_none()
        return None if row is None else _to_inspection_run(row)

    async def list_inspection_runs(
        self, repository_snapshot_id: UUID | None = None
    ) -> tuple[RepositoryInspectionRunData, ...]:
        statement = (
            select(RepositoryInspectionRunRecord)
            .join(RepositorySnapshotRecord)
            .join(RepositoryRecord)
            .where(RepositoryRecord.project_id == self._project_id)
            .order_by(RepositoryInspectionRunRecord.created_at, RepositoryInspectionRunRecord.id)
        )
        if repository_snapshot_id is not None:
            statement = statement.where(
                RepositoryInspectionRunRecord.repository_snapshot_id == repository_snapshot_id
            )
        result = await execute_database(lambda: self._session.execute(statement))
        return tuple(_to_inspection_run(row) for row in result.scalars())

    async def add_inspection_components(
        self, components: tuple[NewRepositoryInspectionComponent, ...]
    ) -> tuple[RepositoryComponentData, ...]:
        if not components:
            return ()
        run_id = components[0].repository_inspection_run_id
        run = await self.get_inspection_run_exact(run_id)
        if run is None or any(
            component.repository_inspection_run_id != run_id
            or component.repository_snapshot_id != run.repository_snapshot_id
            for component in components
        ):
            raise ResourceConflictError(details={"reason": "repository_component_scope"})
        rows = [
            RepositoryComponentRecord(
                id=component.id,
                repository_snapshot_id=component.repository_snapshot_id,
                analysis_stage="stage_b",
                repository_discovery_run_id=None,
                repository_inspection_run_id=component.repository_inspection_run_id,
                path=component.path,
                component_type=component.component_type,
                language=component.language,
                symbol=component.symbol,
                line_start=component.line_start,
                line_end=component.line_end,
                content_excerpt=component.content_excerpt,
                content_hash=component.content_hash,
                relevance_score=component.relevance_score,
            )
            for component in components
        ]
        self._session.add_all(rows)
        await execute_database(self._session.flush)
        return tuple(_to_component(row) for row in rows)

    async def list_inspection_components(
        self, inspection_run_id: UUID
    ) -> tuple[RepositoryComponentData, ...]:
        if await self.get_inspection_run_exact(inspection_run_id) is None:
            return ()
        result = await execute_database(
            lambda: self._session.execute(
                select(RepositoryComponentRecord)
                .where(
                    RepositoryComponentRecord.repository_inspection_run_id == inspection_run_id,
                    RepositoryComponentRecord.analysis_stage == "stage_b",
                )
                .order_by(
                    func.lower(RepositoryComponentRecord.path),
                    RepositoryComponentRecord.path,
                    RepositoryComponentRecord.line_start,
                    RepositoryComponentRecord.id,
                )
            )
        )
        return tuple(_to_component(row) for row in result.scalars())

    async def get_component_exact(self, component_id: UUID) -> RepositoryComponentData | None:
        result = await execute_database(
            lambda: self._session.execute(
                select(RepositoryComponentRecord)
                .join(RepositorySnapshotRecord)
                .join(RepositoryRecord)
                .where(
                    RepositoryComponentRecord.id == component_id,
                    RepositoryRecord.project_id == self._project_id,
                )
            )
        )
        row = result.scalar_one_or_none()
        return _to_component(row) if row is not None else None
