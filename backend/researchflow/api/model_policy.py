"""Transport composition for the configured structured-generation policy."""

from researchflow.config import LlmProvider, Settings
from researchflow.infrastructure.llm.openai_schema import (
    OPENAI_STRICT_SCHEMA_COMPILER_VERSION,
)
from researchflow.ports.providers import JsonValue, StructuredGenerationModelPolicy


def configured_model_policy(settings: Settings) -> StructuredGenerationModelPolicy:
    """Build the exact auditable policy passed through application services."""
    configuration: dict[str, JsonValue]
    if settings.llm_provider is LlmProvider.OPENAI:
        configuration = {
            "api_surface": "responses-v1",
            "automatic_retries": 0,
            "request_timeout_seconds": settings.llm_request_timeout_seconds,
            "store": settings.llm_store_responses,
            "snapshot_pinned": settings.llm_model_snapshot_pinned,
            "schema_compiler_version": OPENAI_STRICT_SCHEMA_COMPILER_VERSION,
            "input_cost_per_million_usd": (
                None
                if settings.llm_input_cost_per_million_usd is None
                else str(settings.llm_input_cost_per_million_usd)
            ),
            "output_cost_per_million_usd": (
                None
                if settings.llm_output_cost_per_million_usd is None
                else str(settings.llm_output_cost_per_million_usd)
            ),
        }
    else:
        configuration = {}
    return StructuredGenerationModelPolicy(
        provider=settings.llm_provider.value,
        model_version=settings.llm_model,
        policy_version=settings.llm_policy_version,
        reasoning_version=settings.llm_reasoning_version,
        temperature=settings.llm_temperature,
        configuration=configuration,
    )
