"""Strict-msgpack LangGraph PostgreSQL checkpointer in an isolated schema."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from types import TracebackType
from typing import Any, Self, cast
from urllib.parse import quote

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from sqlalchemy.engine import make_url

from researchflow.errors import ResourceConflictError

LANGGRAPH_SCHEMA = "langgraph"
_ROOT_CHECKPOINT_NAMESPACE = ""


def checkpoint_connection_string(database_url: str) -> str:
    """Build a credential-preserving psycopg URL scoped only to langgraph schema."""
    url = make_url(database_url)
    rendered = url.set(drivername="postgresql").render_as_string(hide_password=False)
    separator = "&" if "?" in rendered else "?"
    options = quote(f"-csearch_path={LANGGRAPH_SCHEMA}", safe="")
    return f"{rendered}{separator}options={options}"


class PostgresCheckpointStore:
    """Own the saver connection lifecycle; no pickle or arbitrary modules."""

    def __init__(self, database_url: str) -> None:
        self._connection_string = checkpoint_connection_string(database_url)
        self._context: AbstractAsyncContextManager[AsyncPostgresSaver] | None = None
        self._saver: AsyncPostgresSaver | None = None

    @property
    def saver(self) -> AsyncPostgresSaver:
        if self._saver is None:
            raise ResourceConflictError(details={"reason": "checkpoint_store_not_started"})
        return self._saver

    async def __aenter__(self) -> Self:
        serializer = JsonPlusSerializer(
            pickle_fallback=False,
            allowed_json_modules=(),
            allowed_msgpack_modules=(),
        )
        context = AsyncPostgresSaver.from_conn_string(
            self._connection_string,
            serde=serializer,
        )
        self._context = context
        self._saver = await context.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        context = self._context
        self._saver = None
        self._context = None
        if context is not None:
            await context.__aexit__(exc_type, exc_value, traceback)

    async def setup(self) -> None:
        await self.saver.setup()

    async def has_checkpoint(self, *, thread_id: str, namespace: str) -> bool:
        if namespace != "project-analysis":
            raise ResourceConflictError(details={"reason": "checkpoint_namespace_unsupported"})
        config = {
            "configurable": {
                "thread_id": thread_id,
                # LangGraph owns the physical root namespace. ResearchFlow's
                # product-visible namespace remains an explicit WorkflowRun identity.
                "checkpoint_ns": _ROOT_CHECKPOINT_NAMESPACE,
            }
        }
        return await self.saver.aget_tuple(cast(RunnableConfig, config)) is not None

    async def delete_thread(self, thread_id: str) -> None:
        await self.saver.adelete_thread(thread_id)

    def config(self, *, thread_id: str, namespace: str) -> dict[str, Any]:
        if namespace != "project-analysis":
            raise ResourceConflictError(details={"reason": "checkpoint_namespace_unsupported"})
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": _ROOT_CHECKPOINT_NAMESPACE,
            }
        }
