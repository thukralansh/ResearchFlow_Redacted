"""Configuration-selected structured-generation adapter factory."""

from researchflow.config import LlmProvider, Settings
from researchflow.errors import ConfigurationError
from researchflow.infrastructure.llm.fake import FakeStructuredGenerationProvider
from researchflow.infrastructure.llm.openai_responses import (
    OpenAIResponsesStructuredGenerationProvider,
)
from researchflow.ports.providers import StructuredGenerationProvider


def create_structured_generation_provider(settings: Settings) -> StructuredGenerationProvider:
    """Create exactly the configured adapter without fallback or substitution."""
    if settings.llm_provider is LlmProvider.FAKE:
        return FakeStructuredGenerationProvider(
            {},
            model=settings.llm_model,
            reasoning_version=settings.llm_reasoning_version,
        )
    if settings.llm_provider is LlmProvider.OPENAI:
        api_key = settings.llm_api_key.get_secret_value()
        if not api_key:
            raise ConfigurationError(details={"reason": "llm_api_key_missing"})
        return OpenAIResponsesStructuredGenerationProvider(
            api_key=api_key,
            timeout_seconds=settings.llm_request_timeout_seconds,
        )
    raise ConfigurationError(details={"reason": "llm_provider_unknown"})
