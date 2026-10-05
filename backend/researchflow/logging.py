"""Correlated, redacting application logging."""

import json
import logging as stdlib_logging
import re
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Final, TextIO

REDACTED: Final = "[REDACTED]"
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_run_id: ContextVar[str | None] = ContextVar("run_id", default=None)
_project_id: ContextVar[str | None] = ContextVar("project_id", default=None)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(authorization|api[_-]?key|password|secret|token)\s*[:=]\s*[^\s,;]+"
)
_BEARER_TOKEN = re.compile(r"(?i)bearer\s+\S+")


def redact_text(value: str, secrets: Iterable[str] = ()) -> str:
    """Remove configured and structurally recognizable secrets from text."""
    redacted = value
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, REDACTED)
    redacted = _BEARER_TOKEN.sub(f"Bearer {REDACTED}", redacted)
    return _SENSITIVE_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}={REDACTED}",
        redacted,
    )


class _ContextFilter(stdlib_logging.Filter):
    def filter(self, record: stdlib_logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        record.run_id = _run_id.get()
        record.project_id = _project_id.get()
        return True


class _JsonRedactingFormatter(stdlib_logging.Formatter):
    def __init__(self, secrets: Iterable[str]) -> None:
        super().__init__()
        self._secrets = tuple(secret for secret in secrets if secret)

    def format(self, record: stdlib_logging.LogRecord) -> str:
        event: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage(), self._secrets),
        }
        for field in ("request_id", "run_id", "project_id", "error_code"):
            value = getattr(record, field, None)
            if value is not None:
                event[field] = value
        if record.exc_info is not None:
            event["exception"] = redact_text(
                self.formatException(record.exc_info),
                self._secrets,
            )
        return json.dumps(event, sort_keys=True, separators=(",", ":"))


class _ResearchFlowHandler(stdlib_logging.StreamHandler[TextIO]):
    """Marker handler used to keep logging setup idempotent."""


def configure_logging(
    *,
    level: str,
    secrets: Iterable[str] = (),
    stream: TextIO | None = None,
) -> stdlib_logging.Logger:
    """Configure one redacting handler for the ResearchFlow logger."""
    logger = stdlib_logging.getLogger("researchflow")
    logger.setLevel(level)
    logger.propagate = False

    handler = next(
        (existing for existing in logger.handlers if isinstance(existing, _ResearchFlowHandler)),
        None,
    )
    if handler is None:
        handler = _ResearchFlowHandler(stream)
        logger.addHandler(handler)
    elif stream is not None:
        handler.setStream(stream)

    handler.setLevel(level)
    handler.setFormatter(_JsonRedactingFormatter(secrets))
    handler.filters.clear()
    handler.addFilter(_ContextFilter())
    return logger


def get_logger(name: str) -> stdlib_logging.Logger:
    """Return a logger inside the ResearchFlow logging hierarchy."""
    qualified_name = name if name.startswith("researchflow") else f"researchflow.{name}"
    return stdlib_logging.getLogger(qualified_name)


@contextmanager
def bind_log_context(
    *,
    request_id: str | None = None,
    run_id: str | None = None,
    project_id: str | None = None,
) -> Iterator[None]:
    """Bind correlation identifiers for the current execution context."""
    request_token = _request_id.set(request_id)
    run_token = _run_id.set(run_id)
    project_token = _project_id.set(project_id)
    try:
        yield
    finally:
        _project_id.reset(project_token)
        _run_id.reset(run_token)
        _request_id.reset(request_token)
