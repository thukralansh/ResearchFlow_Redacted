"""Deterministic in-memory storage adapter."""

import hashlib

from researchflow.errors import DomainValidationError, ResourceNotFoundError
from researchflow.ports.storage import StoredObject


class FakeStorageProvider:
    """Store byte copies in memory without external or persistence access."""

    def __init__(self) -> None:
        self._objects: dict[str, tuple[bytes, StoredObject]] = {}

    async def put(
        self,
        *,
        object_key: str,
        content: bytes,
        content_type: str,
    ) -> StoredObject:
        if not object_key.strip():
            raise DomainValidationError(details={"field": "object_key"})
        if not content_type.strip():
            raise DomainValidationError(details={"field": "content_type"})

        content_copy = bytes(content)
        stored = StoredObject(
            object_key=object_key,
            content_type=content_type,
            size_bytes=len(content_copy),
            sha256=hashlib.sha256(content_copy).hexdigest(),
        )
        self._objects[object_key] = (content_copy, stored)
        return stored

    async def read(self, *, object_key: str) -> bytes:
        try:
            content, _ = self._objects[object_key]
        except KeyError as error:
            raise ResourceNotFoundError(details={"resource_type": "stored_object"}) from error
        return bytes(content)

    async def delete(self, *, object_key: str) -> None:
        try:
            del self._objects[object_key]
        except KeyError as error:
            raise ResourceNotFoundError(details={"resource_type": "stored_object"}) from error
