"""FastAPI dependency boundaries and transport error mapping."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Final, cast

from fastapi import Depends, Request, status

from researchflow.config import Settings
from researchflow.domain.actor import ActorContext
from researchflow.errors import ErrorCode, InternalError, ResearchFlowError
from researchflow.ports.github import GitHubGateway, GitHubProvider
from researchflow.ports.pdf import PdfParser
from researchflow.ports.project_directory import ProjectDirectory
from researchflow.ports.providers import StructuredGenerationProvider
from researchflow.ports.storage import StorageProvider, StorageProviderFactory
from researchflow.ports.unit_of_work import UnitOfWorkFactory
from researchflow.ports.workflow_launcher import WorkflowCheckpointStore, WorkflowLauncher
from researchflow.prompts.registry import PromptRegistry

HTTP_STATUS_BY_ERROR_CODE: Final[Mapping[ErrorCode, int]] = MappingProxyType(
    {
        ErrorCode.CONFIG_INVALID: status.HTTP_500_INTERNAL_SERVER_ERROR,
        ErrorCode.ACTOR_DEVELOPMENT_FORBIDDEN: status.HTTP_403_FORBIDDEN,
        ErrorCode.VALIDATION_INVALID: status.HTTP_422_UNPROCESSABLE_CONTENT,
        ErrorCode.RESOURCE_NOT_FOUND: status.HTTP_404_NOT_FOUND,
        ErrorCode.RESOURCE_CONFLICT: status.HTTP_409_CONFLICT,
        ErrorCode.PROVIDER_UNKNOWN_TASK: status.HTTP_422_UNPROCESSABLE_CONTENT,
        ErrorCode.PROVIDER_INVALID_OUTPUT: status.HTTP_502_BAD_GATEWAY,
        ErrorCode.PROVIDER_FAILURE: status.HTTP_502_BAD_GATEWAY,
        ErrorCode.EXTERNAL_FAILURE: status.HTTP_503_SERVICE_UNAVAILABLE,
        ErrorCode.INTERNAL_ERROR: status.HTTP_500_INTERNAL_SERVER_ERROR,
    }
)


def http_status_for_error(error: ResearchFlowError) -> int:
    """Map a transport-neutral error at the API boundary."""
    return HTTP_STATUS_BY_ERROR_CODE.get(
        error.code,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


def normalize_exception(error: Exception) -> ResearchFlowError:
    """Normalize unknown failures without exposing their diagnostics."""
    if isinstance(error, ResearchFlowError):
        return error
    return InternalError(cause=error)


def get_actor_context(request: Request) -> ActorContext:
    actor_context = getattr(request.app.state, "actor_context", None)
    if not isinstance(actor_context, ActorContext):
        raise InternalError(details={"dependency": "actor_context"})
    return actor_context


def get_runtime_settings(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    if not isinstance(settings, Settings):
        raise InternalError(details={"dependency": "settings"})
    return settings


def get_unit_of_work_factory(request: Request) -> UnitOfWorkFactory:
    factory = getattr(request.app.state, "unit_of_work_factory", None)
    if factory is None or not callable(factory):
        raise InternalError(details={"dependency": "unit_of_work_factory"})
    return cast(UnitOfWorkFactory, factory)


def get_project_directory(request: Request) -> ProjectDirectory:
    directory = getattr(request.app.state, "project_directory", None)
    if directory is None or not hasattr(directory, "list_for_actor"):
        raise InternalError(details={"dependency": "project_directory"})
    return cast(ProjectDirectory, directory)


def get_storage_provider(request: Request) -> StorageProvider:
    factory = getattr(request.app.state, "storage_provider_factory", None)
    if factory is None or not callable(factory):
        raise InternalError(details={"dependency": "storage_provider_factory"})
    provider = cast(StorageProviderFactory, factory)()
    if not isinstance(provider, StorageProvider):
        raise InternalError(details={"dependency": "storage_provider"})
    return provider


def get_pdf_parser(request: Request) -> PdfParser:
    parser = getattr(request.app.state, "pdf_parser", None)
    if not isinstance(parser, PdfParser):
        raise InternalError(details={"dependency": "pdf_parser"})
    return parser


def get_github_gateway(request: Request) -> GitHubGateway:
    gateway = getattr(request.app.state, "github_gateway", None)
    if not isinstance(gateway, GitHubGateway):
        raise InternalError(details={"dependency": "github_gateway"})
    return gateway


def get_github_provider(request: Request) -> GitHubProvider:
    provider = getattr(request.app.state, "github_gateway", None)
    if not isinstance(provider, GitHubProvider):
        raise InternalError(details={"dependency": "github_provider"})
    return provider


def get_structured_generation_provider(request: Request) -> StructuredGenerationProvider:
    provider = getattr(request.app.state, "structured_generation_provider", None)
    if not isinstance(provider, StructuredGenerationProvider):
        raise InternalError(details={"dependency": "structured_generation_provider"})
    return provider


def get_prompt_registry(request: Request) -> PromptRegistry:
    registry = getattr(request.app.state, "prompt_registry", None)
    if not isinstance(registry, PromptRegistry):
        raise InternalError(details={"dependency": "prompt_registry"})
    return registry


def get_workflow_launcher(request: Request) -> WorkflowLauncher:
    launcher = getattr(request.app.state, "workflow_launcher", None)
    if launcher is None:
        raise InternalError(details={"dependency": "workflow_launcher"})
    return cast(WorkflowLauncher, launcher)


def get_workflow_checkpoint_store(request: Request) -> WorkflowCheckpointStore:
    store = getattr(request.app.state, "workflow_checkpoint_store", None)
    if store is None:
        raise InternalError(details={"dependency": "workflow_checkpoint_store"})
    return cast(WorkflowCheckpointStore, store)


ActorDependency = Annotated[ActorContext, Depends(get_actor_context)]
SettingsDependency = Annotated[Settings, Depends(get_runtime_settings)]
UnitOfWorkFactoryDependency = Annotated[UnitOfWorkFactory, Depends(get_unit_of_work_factory)]
ProjectDirectoryDependency = Annotated[ProjectDirectory, Depends(get_project_directory)]
StorageDependency = Annotated[StorageProvider, Depends(get_storage_provider)]
PdfParserDependency = Annotated[PdfParser, Depends(get_pdf_parser)]
GitHubGatewayDependency = Annotated[GitHubGateway, Depends(get_github_gateway)]
GitHubProviderDependency = Annotated[GitHubProvider, Depends(get_github_provider)]
StructuredGenerationProviderDependency = Annotated[
    StructuredGenerationProvider, Depends(get_structured_generation_provider)
]
PromptRegistryDependency = Annotated[PromptRegistry, Depends(get_prompt_registry)]
WorkflowLauncherDependency = Annotated[WorkflowLauncher, Depends(get_workflow_launcher)]
WorkflowCheckpointStoreDependency = Annotated[
    WorkflowCheckpointStore, Depends(get_workflow_checkpoint_store)
]
