"""Binary object storage interface."""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class StoredObject:
    object_key: str
    content_type: str
    size_bytes: int
    sha256: str


@runtime_checkable
class StorageProvider(Protocol):
    """Storage operations used through application services."""

    async def put(
        self,
        *,
        object_key: str,
        content: bytes,
        content_type: str,
    ) -> StoredObject: ...

    async def read(self, *, object_key: str) -> bytes: ...

    async def delete(self, *, object_key: str) -> None: ...


class StorageProviderFactory(Protocol):
    """Construct or return the application-scoped Storage adapter."""

    def __call__(self) -> StorageProvider: ...
