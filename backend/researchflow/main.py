"""FastAPI application composition root."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from researchflow import __version__
from researchflow.api.dependencies import http_status_for_error, normalize_exception
from researchflow.api.model_policy import configured_model_policy
from researchflow.api.routes.blueprint_eligibility import router as blueprint_eligibility_router
from researchflow.api.routes.blueprints import router as blueprints_router
from researchflow.api.routes.copilot import router as copilot_router
from researchflow.api.routes.extraction import router as extraction_router
from researchflow.api.routes.judgments import router as judgments_router
from researchflow.api.routes.knowledge import router as knowledge_router
from researchflow.api.routes.mapping_review import router as mapping_review_router
from researchflow.api.routes.mappings import router as mappings_router
from researchflow.api.routes.mental_model_assessments import (
    router as mental_model_assessments_router,
)
from researchflow.api.routes.mental_models import router as mental_models_router
from researchflow.api.routes.method_model_reassessment import (
    router as method_model_reassessment_router,
)
from researchflow.api.routes.method_models import router as method_models_router
from researchflow.api.routes.projects import router as projects_router
from researchflow.api.routes.repositories import router as repositories_router
from researchflow.api.routes.repository_discovery import router as repository_discovery_router
from researchflow.api.routes.repository_inspection import router as repository_inspection_router
from researchflow.api.routes.research_questions import router as research_questions_router
from researchflow.api.routes.review_gates import router as review_gates_router
from researchflow.api.routes.source_processing import router as source_processing_router
from researchflow.api.routes.sources import router as sources_router
from researchflow.api.routes.staleness import router as staleness_router
from researchflow.api.routes.verification import router as verification_router
from researchflow.api.routes.workflows import router as workflows_router
from researchflow.application.extraction import ExtractionService
from researchflow.application.generation import StructuredGenerationService
from researchflow.application.mental_models import MentalModelService
from researchflow.application.method_extraction import MethodExtractionService
from researchflow.application.project_analysis_workflow import ProjectAnalysisWorkflowContext
from researchflow.application.repository_discovery import RepositoryDiscoveryService
from researchflow.application.review_gates import PrimarySourceReviewGateService
from researchflow.application.workflow_runs import WorkflowLifecycleNodeContext
from researchflow.config import Settings, get_settings
from researchflow.domain.actor import ActorContext, ActorType, RuntimeEnvironment
from researchflow.domain.repository_roles import DiscoveryLimits
from researchflow.errors import (
    DevelopmentActorForbiddenError,
    DomainValidationError,
    ResearchFlowError,
)
from researchflow.infrastructure.checkpoints.postgres import PostgresCheckpointStore
from researchflow.infrastructure.database.project_directory import SqlAlchemyProjectDirectory
from researchflow.infrastructure.database.session import (
    create_database_engine,
    create_session_factory,
)
from researchflow.infrastructure.database.uow import SqlAlchemyUnitOfWorkFactory
from researchflow.infrastructure.github.api import GitHubApiGateway
from researchflow.infrastructure.llm.factory import create_structured_generation_provider
from researchflow.infrastructure.llm.openai_responses import (
    OpenAIResponsesStructuredGenerationProvider,
)
from researchflow.infrastructure.pdf.pymupdf_parser import PyMuPdfParser
from researchflow.infrastructure.storage.supabase import SupabaseStorageProvider
from researchflow.infrastructure.workflows.in_process import InProcessWorkflowLauncher
from researchflow.logging import configure_logging
from researchflow.ports.github import GitHubGateway
from researchflow.ports.pdf import PdfParser
from researchflow.ports.project_directory import ProjectDirectory
from researchflow.ports.providers import StructuredGenerationProvider
from researchflow.ports.storage import StorageProvider
from researchflow.ports.unit_of_work import UnitOfWorkFactory
from researchflow.ports.workflow_launcher import WorkflowCheckpointStore, WorkflowLauncher
from researchflow.prompts.registry import PromptRegistry
from researchflow.workflows.project_analysis import build_project_analysis_graph

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    unit_of_work_factory: UnitOfWorkFactory | None = None,
    project_directory: ProjectDirectory | None = None,
    storage_provider: StorageProvider | None = None,
    pdf_parser: PdfParser | None = None,
    github_gateway: GitHubGateway | None = None,
    structured_generation_provider: StructuredGenerationProvider | None = None,
    prompt_registry: PromptRegistry | None = None,
    actor_context: ActorContext | None = None,
    workflow_launcher: WorkflowLauncher | None = None,
    workflow_checkpoint_store: WorkflowCheckpointStore | None = None,
) -> FastAPI:
    """Construct the ResearchFlow API application."""
    runtime_settings = settings or get_settings()
    runtime_actor = actor_context or ActorContext.development(runtime_settings.environment_mode)
    if (
        runtime_settings.environment_mode is RuntimeEnvironment.EXTERNAL
        and runtime_actor.actor_type is ActorType.DEVELOPMENT
    ):
        raise DevelopmentActorForbiddenError()
    configure_logging(
        level=runtime_settings.log_level.value,
        secrets=runtime_settings.secret_values(),
    )

    if (unit_of_work_factory is None) != (project_directory is None):
        raise ValueError("unit_of_work_factory and project_directory must be provided together")

    owned_engine = None
    runtime_uow_factory = unit_of_work_factory
    runtime_project_directory = project_directory
    if runtime_uow_factory is None or runtime_project_directory is None:
        owned_engine = create_database_engine(runtime_settings)
        session_factory = create_session_factory(owned_engine)
        runtime_uow_factory = SqlAlchemyUnitOfWorkFactory(session_factory)
        runtime_project_directory = SqlAlchemyProjectDirectory(session_factory)

    owned_storage_provider: SupabaseStorageProvider | None = None
    owned_github_gateway: GitHubApiGateway | None = None
    owned_generation_provider: OpenAIResponsesStructuredGenerationProvider | None = None
    owned_checkpoint_store: PostgresCheckpointStore | None = None
    owned_workflow_launcher: InProcessWorkflowLauncher | None = None

    def storage_provider_factory() -> StorageProvider:
        nonlocal owned_storage_provider
        if storage_provider is not None:
            return storage_provider
        if owned_storage_provider is None:
            owned_storage_provider = SupabaseStorageProvider(
                supabase_url=str(runtime_settings.supabase_url),
                service_role_key=runtime_settings.supabase_service_role_key.get_secret_value(),
                bucket_name=runtime_settings.supabase_storage_bucket,
                max_upload_bytes=runtime_settings.max_upload_bytes,
            )
        return owned_storage_provider

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        nonlocal owned_checkpoint_store, owned_github_gateway, owned_workflow_launcher
        checkpoint_entered = False
        try:
            if (
                workflow_launcher is None
                and workflow_checkpoint_store is None
                and owned_engine is not None
            ):
                owned_checkpoint_store = PostgresCheckpointStore(
                    runtime_settings.database_url.get_secret_value()
                )
                await owned_checkpoint_store.__aenter__()
                checkpoint_entered = True
                await owned_checkpoint_store.setup()
                generation_service = StructuredGenerationService(
                    uow_factory=runtime_uow_factory,
                    provider=application.state.structured_generation_provider,
                    prompt_registry=application.state.prompt_registry,
                    max_context_bytes=runtime_settings.max_generation_context_bytes,
                )
                lifecycle_context = WorkflowLifecycleNodeContext(uow_factory=runtime_uow_factory)
                review_gate = PrimarySourceReviewGateService(
                    actor=runtime_actor, uow_factory=runtime_uow_factory
                )
                node_context = ProjectAnalysisWorkflowContext(
                    actor=runtime_actor,
                    uow_factory=runtime_uow_factory,
                    lifecycle=lifecycle_context,
                    extraction=ExtractionService(
                        actor=runtime_actor,
                        uow_factory=runtime_uow_factory,
                        generation_service=generation_service,
                    ),
                    method_extraction=MethodExtractionService(
                        uow_factory=runtime_uow_factory,
                        generation_service=generation_service,
                    ),
                    mental_models=MentalModelService(
                        actor=runtime_actor,
                        uow_factory=runtime_uow_factory,
                        generation_service=generation_service,
                        review_gate_service=review_gate,
                    ),
                    review_gate=review_gate,
                    repository_discovery=RepositoryDiscoveryService(
                        actor=runtime_actor,
                        uow_factory=runtime_uow_factory,
                        github=application.state.github_gateway,
                    ),
                    model_policy=configured_model_policy(runtime_settings),
                    repository_discovery_version=runtime_settings.repository_discovery_version,
                    repository_limits=DiscoveryLimits(
                        tree_entry_limit=runtime_settings.repository_tree_entry_limit,
                        structural_file_limit=runtime_settings.repository_file_inspection_limit,
                        max_file_bytes=runtime_settings.repository_max_file_bytes,
                    ),
                )
                graph = build_project_analysis_graph(
                    context=node_context,
                    checkpointer=owned_checkpoint_store.saver,
                )
                owned_workflow_launcher = InProcessWorkflowLauncher(
                    graph=graph,
                    failure_handler=lambda state, code: lifecycle_context.fail(
                        state, error_code=code
                    ),
                    recovery_failure_handler=lifecycle_context.fail_run,
                )
                application.state.workflow_checkpoint_store = owned_checkpoint_store
                application.state.workflow_launcher = owned_workflow_launcher
            yield
        finally:
            if owned_workflow_launcher is not None:
                await owned_workflow_launcher.close()
            if owned_checkpoint_store is not None and checkpoint_entered:
                await owned_checkpoint_store.__aexit__(None, None, None)
            if owned_storage_provider is not None:
                await owned_storage_provider.close()
            if owned_engine is not None:
                await owned_engine.dispose()
            if owned_github_gateway is not None:
                await owned_github_gateway.close()
            if owned_generation_provider is not None:
                await owned_generation_provider.close()

    application = FastAPI(
        title="ResearchFlow API",
        version=__version__,
        lifespan=lifespan,
    )
    application.state.settings = runtime_settings
    application.state.actor_context = runtime_actor
    application.state.unit_of_work_factory = runtime_uow_factory
    application.state.project_directory = runtime_project_directory
    application.state.storage_provider_factory = storage_provider_factory
    application.state.pdf_parser = pdf_parser or PyMuPdfParser(
        version=runtime_settings.parser_version,
        maximum_pages=runtime_settings.max_pdf_pages,
    )
    runtime_generation_provider = structured_generation_provider
    if runtime_generation_provider is None:
        runtime_generation_provider = create_structured_generation_provider(runtime_settings)
        if isinstance(runtime_generation_provider, OpenAIResponsesStructuredGenerationProvider):
            owned_generation_provider = runtime_generation_provider
    application.state.structured_generation_provider = runtime_generation_provider
    application.state.prompt_registry = prompt_registry or PromptRegistry()
    if (workflow_launcher is None) != (workflow_checkpoint_store is None):
        raise ValueError(
            "workflow_launcher and workflow_checkpoint_store must be provided together"
        )
    application.state.workflow_launcher = workflow_launcher
    application.state.workflow_checkpoint_store = workflow_checkpoint_store
    if github_gateway is None:
        owned_github_gateway = GitHubApiGateway(
            token=runtime_settings.github_token.get_secret_value()
        )
        application.state.github_gateway = owned_github_gateway
    else:
        application.state.github_gateway = github_gateway

    @application.exception_handler(ResearchFlowError)
    async def researchflow_error_handler(_: Request, error: ResearchFlowError) -> JSONResponse:
        return JSONResponse(
            status_code=http_status_for_error(error),
            content=error.as_public_dict(),
        )

    @application.exception_handler(RequestValidationError)
    async def request_validation_error_handler(
        _: Request, __: RequestValidationError
    ) -> JSONResponse:
        error = DomainValidationError(details={"reason": "request_validation_failed"})
        return JSONResponse(
            status_code=http_status_for_error(error),
            content=error.as_public_dict(),
        )

    @application.exception_handler(Exception)
    async def unknown_error_handler(_: Request, error: Exception) -> JSONResponse:
        normalized = normalize_exception(error)
        logger.exception("Unhandled API error", extra={"error_code": normalized.code.value})
        return JSONResponse(
            status_code=http_status_for_error(normalized),
            content=normalized.as_public_dict(),
        )

    application.include_router(projects_router)
    application.include_router(research_questions_router)
    application.include_router(sources_router)
    application.include_router(source_processing_router)
    application.include_router(repositories_router)
    application.include_router(repository_discovery_router)
    application.include_router(repository_inspection_router)
    application.include_router(extraction_router)
    application.include_router(method_models_router)
    application.include_router(method_model_reassessment_router)
    application.include_router(mental_model_assessments_router)
    application.include_router(mental_models_router)
    application.include_router(mappings_router)
    application.include_router(mapping_review_router)
    application.include_router(knowledge_router)
    application.include_router(judgments_router)
    application.include_router(review_gates_router)
    application.include_router(staleness_router)
    application.include_router(blueprint_eligibility_router)
    application.include_router(blueprints_router)
    application.include_router(copilot_router)
    application.include_router(verification_router)
    application.include_router(workflows_router)
    return application


app = create_app()
