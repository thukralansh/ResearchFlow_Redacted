"""Typed runtime configuration."""

import re
from decimal import Decimal
from enum import StrEnum
from functools import lru_cache
from typing import Final

from pydantic import AnyHttpUrl, Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from researchflow.domain.actor import RuntimeEnvironment
from researchflow.errors import ConfigurationError

_LOCAL_DATABASE_URL: Final = "postgresql+psycopg://postgres:postgres@127.0.0.1:54322/postgres"
_LOCAL_TEST_DATABASE_URL: Final = "postgresql+psycopg://postgres:postgres@127.0.0.1:55322/postgres"
_OPENAI_TERRA_MODEL: Final = re.compile(r"^gpt-5\.6-terra(?:-\d{4}-\d{2}-\d{2})?$")
_OPENAI_TERRA_REASONING: Final = frozenset({"none", "low", "medium", "high", "xhigh", "max"})


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class PersistenceEnvironment(StrEnum):
    """Explicitly separate disposable local state from hosted development."""

    LOCAL = "local"
    HOSTED_DEVELOPMENT = "hosted_development"


class DatabaseConnectionMode(StrEnum):
    """Approved PostgreSQL connection modes for migrations and the backend."""

    DIRECT = "direct"
    SESSION_POOLER = "session_pooler"


class LlmProvider(StrEnum):
    """Configured structured-generation adapters."""

    FAKE = "fake"
    OPENAI = "openai"


class Settings(BaseSettings):
    """Validated process configuration with redacted secret fields."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
        validate_default=True,
    )

    environment_mode: RuntimeEnvironment = Field(
        default=RuntimeEnvironment.LOCAL,
        validation_alias="ENVIRONMENT_MODE",
    )
    persistence_environment: PersistenceEnvironment = Field(
        default=PersistenceEnvironment.LOCAL,
        validation_alias="PERSISTENCE_ENVIRONMENT",
    )
    database_connection_mode: DatabaseConnectionMode = Field(
        default=DatabaseConnectionMode.DIRECT,
        validation_alias="DATABASE_CONNECTION_MODE",
    )
    database_url: SecretStr = Field(
        default=SecretStr(_LOCAL_DATABASE_URL),
        validation_alias="DATABASE_URL",
    )
    test_database_url: SecretStr = Field(
        default=SecretStr(_LOCAL_TEST_DATABASE_URL),
        validation_alias="TEST_DATABASE_URL",
    )
    supabase_url: AnyHttpUrl = Field(
        default=AnyHttpUrl("http://127.0.0.1:54321"),
        validation_alias="SUPABASE_URL",
    )
    supabase_project_ref: str = Field(
        default="",
        validation_alias="SUPABASE_PROJECT_REF",
    )
    supabase_anon_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="SUPABASE_ANON_KEY",
    )
    supabase_service_role_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="SUPABASE_SERVICE_ROLE_KEY",
    )
    supabase_storage_bucket: str = Field(
        default="researchflow-sources",
        min_length=1,
        validation_alias="SUPABASE_STORAGE_BUCKET",
    )
    github_token: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="GITHUB_TOKEN",
    )
    llm_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="LLM_API_KEY",
    )
    llm_provider: LlmProvider = Field(
        default=LlmProvider.FAKE,
        validation_alias="LLM_PROVIDER",
    )
    llm_model: str = Field(
        default="fake-v1",
        min_length=1,
        validation_alias="LLM_MODEL",
    )
    llm_policy_version: str = Field(
        default="fake-policy-v1",
        min_length=1,
        validation_alias="LLM_POLICY_VERSION",
    )
    llm_reasoning_version: str = Field(
        default="fake-reasoning-v1",
        min_length=1,
        validation_alias="LLM_REASONING_VERSION",
    )
    llm_temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=2.0,
        validation_alias="LLM_TEMPERATURE",
    )
    llm_request_timeout_seconds: float = Field(
        default=180.0,
        gt=0,
        validation_alias="LLM_REQUEST_TIMEOUT_SECONDS",
    )
    llm_store_responses: bool = Field(
        default=False,
        validation_alias="LLM_STORE_RESPONSES",
    )
    llm_model_snapshot_pinned: bool = Field(
        default=False,
        validation_alias="LLM_MODEL_SNAPSHOT_PINNED",
    )
    llm_input_cost_per_million_usd: Decimal | None = Field(
        default=None,
        ge=0,
        validation_alias="LLM_INPUT_COST_PER_MILLION_USD",
    )
    llm_output_cost_per_million_usd: Decimal | None = Field(
        default=None,
        ge=0,
        validation_alias="LLM_OUTPUT_COST_PER_MILLION_USD",
    )
    max_generation_context_bytes: int = Field(
        default=1_048_576,
        gt=0,
        validation_alias="MAX_GENERATION_CONTEXT_BYTES",
    )
    prompt_version: str = Field(
        default="v1",
        min_length=1,
        validation_alias="PROMPT_VERSION",
    )
    parser_version: str = Field(
        default="pymupdf-v1",
        min_length=1,
        validation_alias="PARSER_VERSION",
    )
    max_upload_bytes: int = Field(
        default=52_428_800,
        gt=0,
        validation_alias="MAX_UPLOAD_BYTES",
    )
    max_pdf_pages: int = Field(
        default=500,
        gt=0,
        validation_alias="MAX_PDF_PAGES",
    )
    repository_tree_entry_limit: int = Field(
        default=5_000,
        gt=0,
        validation_alias="REPOSITORY_TREE_ENTRY_LIMIT",
    )
    repository_file_inspection_limit: int = Field(
        default=20,
        gt=0,
        validation_alias="REPOSITORY_FILE_INSPECTION_LIMIT",
    )
    repository_max_file_bytes: int = Field(
        default=262_144,
        gt=0,
        validation_alias="REPOSITORY_MAX_FILE_BYTES",
    )
    repository_discovery_version: str = Field(
        default="rf-structural-discovery-v1",
        min_length=1,
        validation_alias="REPOSITORY_DISCOVERY_VERSION",
    )
    repository_stage_b_candidate_limit: int = Field(
        default=30,
        gt=0,
        validation_alias="REPOSITORY_STAGE_B_CANDIDATE_LIMIT",
    )
    repository_stage_b_inspection_limit: int = Field(
        default=12,
        gt=0,
        validation_alias="REPOSITORY_STAGE_B_INSPECTION_LIMIT",
    )
    repository_stage_b_aggregate_bytes: int = Field(
        default=3_145_728,
        gt=0,
        validation_alias="REPOSITORY_STAGE_B_AGGREGATE_BYTES",
    )
    repository_ranking_version: str = Field(
        default="rf-repository-ranking-v1",
        min_length=1,
        validation_alias="REPOSITORY_RANKING_VERSION",
    )
    repository_analysis_version: str = Field(
        default="rf-python-symbols-v1",
        min_length=1,
        validation_alias="REPOSITORY_ANALYSIS_VERSION",
    )
    log_level: LogLevel = Field(
        default=LogLevel.INFO,
        validation_alias="LOG_LEVEL",
    )
    database_pool_size: int = Field(
        default=5,
        gt=0,
        validation_alias="DATABASE_POOL_SIZE",
    )
    database_max_overflow: int = Field(
        default=5,
        ge=0,
        validation_alias="DATABASE_MAX_OVERFLOW",
    )
    database_pool_timeout_seconds: int = Field(
        default=30,
        gt=0,
        validation_alias="DATABASE_POOL_TIMEOUT_SECONDS",
    )
    database_connect_timeout_seconds: int = Field(
        default=10,
        gt=0,
        validation_alias="DATABASE_CONNECT_TIMEOUT_SECONDS",
    )
    database_statement_timeout_ms: int = Field(
        default=30_000,
        gt=0,
        validation_alias="DATABASE_STATEMENT_TIMEOUT_MS",
    )

    @model_validator(mode="after")
    def validate_persistence_environment(self) -> "Settings":
        """Require complete, internally consistent hosted configuration."""
        if self.llm_provider is LlmProvider.OPENAI:
            if not self.llm_api_key.get_secret_value():
                raise ValueError("OpenAI provider requires LLM_API_KEY")
            if self.llm_store_responses:
                raise ValueError("OpenAI response storage must remain disabled")
            if _OPENAI_TERRA_MODEL.fullmatch(self.llm_model) is None:
                raise ValueError("OpenAI provider has an unknown configured model")
            is_dated_snapshot = self.llm_model != "gpt-5.6-terra"
            if self.llm_model_snapshot_pinned != is_dated_snapshot:
                raise ValueError("OpenAI model snapshot policy does not match model identifier")
            if self.llm_reasoning_version not in _OPENAI_TERRA_REASONING:
                raise ValueError("OpenAI provider has an unknown reasoning effort")
            if self.llm_reasoning_version != "none" and self.llm_temperature != 0:
                raise ValueError("temperature is unsupported at configured reasoning effort")
            if (
                self.llm_input_cost_per_million_usd is None
                or self.llm_output_cost_per_million_usd is None
            ):
                raise ValueError("OpenAI provider requires explicit invocation cost rates")
        elif self.environment_mode is not RuntimeEnvironment.LOCAL:
            raise ValueError("fake provider is restricted to explicit local/test use")

        if self.persistence_environment is not PersistenceEnvironment.HOSTED_DEVELOPMENT:
            return self

        if self.environment_mode is not RuntimeEnvironment.INTERNAL:
            raise ValueError("hosted development requires internal runtime mode")
        if len(self.supabase_project_ref) != 20 or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789"
            for character in self.supabase_project_ref
        ):
            raise ValueError("hosted development requires a valid Supabase project reference")
        if self.supabase_project_ref not in str(self.supabase_url):
            raise ValueError("Supabase URL does not match the hosted project reference")
        if not self.supabase_anon_key.get_secret_value():
            raise ValueError("hosted development requires a Supabase publishable key")
        if not self.supabase_service_role_key.get_secret_value():
            raise ValueError("hosted development requires a Supabase backend secret key")
        return self

    def secret_values(self) -> tuple[str, ...]:
        """Return configured secret values for server-side log redaction."""
        secrets = (
            self.database_url,
            self.test_database_url,
            self.supabase_anon_key,
            self.supabase_service_role_key,
            self.github_token,
            self.llm_api_key,
        )
        return tuple(value for secret in secrets if (value := secret.get_secret_value()))

    def safe_summary(self) -> dict[str, object]:
        """Return non-secret diagnostics suitable for startup logs."""
        return {
            "environment_mode": self.environment_mode.value,
            "persistence_environment": self.persistence_environment.value,
            "database_connection_mode": self.database_connection_mode.value,
            "supabase_url": str(self.supabase_url),
            "supabase_storage_bucket": self.supabase_storage_bucket,
            "github_token_configured": bool(self.github_token.get_secret_value()),
            "llm_api_key_configured": bool(self.llm_api_key.get_secret_value()),
            "llm_provider": self.llm_provider.value,
            "llm_model": self.llm_model,
            "llm_policy_version": self.llm_policy_version,
            "llm_reasoning_version": self.llm_reasoning_version,
            "llm_temperature": self.llm_temperature,
            "llm_request_timeout_seconds": self.llm_request_timeout_seconds,
            "llm_store_responses": self.llm_store_responses,
            "llm_model_snapshot_pinned": self.llm_model_snapshot_pinned,
            "llm_input_cost_per_million_usd": (
                None
                if self.llm_input_cost_per_million_usd is None
                else str(self.llm_input_cost_per_million_usd)
            ),
            "llm_output_cost_per_million_usd": (
                None
                if self.llm_output_cost_per_million_usd is None
                else str(self.llm_output_cost_per_million_usd)
            ),
            "max_generation_context_bytes": self.max_generation_context_bytes,
            "prompt_version": self.prompt_version,
            "parser_version": self.parser_version,
            "repository_discovery_version": self.repository_discovery_version,
            "repository_tree_entry_limit": self.repository_tree_entry_limit,
            "repository_file_inspection_limit": self.repository_file_inspection_limit,
            "repository_max_file_bytes": self.repository_max_file_bytes,
            "repository_stage_b_candidate_limit": self.repository_stage_b_candidate_limit,
            "repository_stage_b_inspection_limit": self.repository_stage_b_inspection_limit,
            "repository_stage_b_aggregate_bytes": self.repository_stage_b_aggregate_bytes,
            "repository_ranking_version": self.repository_ranking_version,
            "repository_analysis_version": self.repository_analysis_version,
            "log_level": self.log_level.value,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and cache settings, exposing only a safe configuration error."""
    try:
        return Settings()
    except ValidationError as error:
        raise ConfigurationError(
            details={"reason": "settings_validation_failed"},
            cause=error,
        ) from error


def clear_settings_cache() -> None:
    """Clear process settings for controlled test and reload boundaries."""
    get_settings.cache_clear()
