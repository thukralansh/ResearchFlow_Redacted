"""Supabase Storage adapter for private source objects."""

import hashlib
from pathlib import PurePosixPath
from typing import Protocol, cast

from storage3 import AsyncStorageClient
from storage3.exceptions import StorageApiError
from storage3.types import CreateOrUpdateBucketOptions, FileOptions

from researchflow.errors import (
    ConfigurationError,
    DomainValidationError,
    ExternalServiceError,
    ResourceNotFoundError,
)
from researchflow.ports.storage import StoredObject


class _BucketClient(Protocol):
    async def upload(self, path: str, file: bytes, file_options: FileOptions) -> object: ...

    async def download(self, path: str) -> bytes: ...

    async def remove(self, paths: list[str]) -> list[dict[str, object]]: ...


class _HttpSession(Protocol):
    async def aclose(self) -> None: ...


class _BucketMetadata(Protocol):
    public: bool
    file_size_limit: int | None
    allowed_mime_types: list[str] | None


class _StorageClient(Protocol):
    @property
    def session(self) -> _HttpSession: ...

    def from_(self, bucket_id: str) -> _BucketClient: ...

    async def get_bucket(self, bucket_id: str) -> _BucketMetadata: ...

    async def create_bucket(
        self,
        bucket_id: str,
        name: str | None = None,
        options: CreateOrUpdateBucketOptions | None = None,
    ) -> dict[str, str]: ...

    async def update_bucket(
        self,
        bucket_id: str,
        options: CreateOrUpdateBucketOptions,
    ) -> dict[str, str]: ...


def validate_storage_key(object_key: str) -> str:
    """Return one canonical relative POSIX key or reject traversal/ambiguity."""
    normalized = object_key.strip()
    path = PurePosixPath(normalized)
    raw_parts = normalized.split("/")
    if (
        not normalized
        or normalized.startswith("/")
        or "\\" in normalized
        or any(part in {"", ".", ".."} for part in raw_parts)
        or str(path) != normalized
    ):
        raise DomainValidationError(details={"field": "object_key"})
    return normalized


class SupabaseStorageProvider:
    """Store objects in one configured private Supabase bucket."""

    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        bucket_name: str,
        max_upload_bytes: int,
        client: _StorageClient | None = None,
    ) -> None:
        if not service_role_key:
            raise ConfigurationError(details={"field": "service_role_key"})
        if not bucket_name.strip():
            raise ConfigurationError(details={"field": "bucket_name"})

        storage_url = f"{supabase_url.rstrip('/')}/storage/v1/"
        headers = {
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
        }
        self._client = client or cast(
            _StorageClient,
            AsyncStorageClient(storage_url, headers),
        )
        self._bucket_name = bucket_name
        self._max_upload_bytes = max_upload_bytes

    async def ensure_private_bucket(self) -> None:
        """Create or correct the configured bucket without duplicating it."""
        options: CreateOrUpdateBucketOptions = {
            "public": False,
            "file_size_limit": self._max_upload_bytes,
            "allowed_mime_types": ["application/pdf", "application/octet-stream"],
        }
        try:
            bucket = await self._client.get_bucket(self._bucket_name)
        except StorageApiError as error:
            if str(error.status) != "404":
                raise self._external_error("get_bucket", error) from error
            try:
                await self._client.create_bucket(self._bucket_name, options=options)
            except StorageApiError as create_error:
                raise self._external_error("create_bucket", create_error) from create_error
            return

        if (
            bucket.public
            or bucket.file_size_limit != self._max_upload_bytes
            or bucket.allowed_mime_types != options["allowed_mime_types"]
        ):
            try:
                await self._client.update_bucket(self._bucket_name, options)
            except StorageApiError as error:
                raise self._external_error("update_bucket", error) from error

    async def put(
        self,
        *,
        object_key: str,
        content: bytes,
        content_type: str,
    ) -> StoredObject:
        canonical_key = validate_storage_key(object_key)
        if not content_type.strip():
            raise DomainValidationError(details={"field": "content_type"})
        if len(content) > self._max_upload_bytes:
            raise DomainValidationError(details={"field": "content_size"})

        try:
            await self._client.from_(self._bucket_name).upload(
                canonical_key,
                bytes(content),
                {"content-type": content_type, "upsert": "true"},
            )
        except StorageApiError as error:
            raise self._external_error("upload", error) from error

        return StoredObject(
            object_key=canonical_key,
            content_type=content_type,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )

    async def read(self, *, object_key: str) -> bytes:
        canonical_key = validate_storage_key(object_key)
        try:
            return await self._client.from_(self._bucket_name).download(canonical_key)
        except StorageApiError as error:
            if str(error.status) == "404":
                raise ResourceNotFoundError(details={"resource_type": "stored_object"}) from error
            raise self._external_error("download", error) from error

    async def delete(self, *, object_key: str) -> None:
        canonical_key = validate_storage_key(object_key)
        try:
            await self._client.from_(self._bucket_name).remove([canonical_key])
        except StorageApiError as error:
            if str(error.status) == "404":
                raise ResourceNotFoundError(details={"resource_type": "stored_object"}) from error
            raise self._external_error("delete", error) from error

    async def close(self) -> None:
        """Close the adapter-owned HTTP client."""
        await self._client.session.aclose()

    @staticmethod
    def _external_error(operation: str, error: StorageApiError) -> ExternalServiceError:
        return ExternalServiceError(
            details={"service_name": "supabase_storage", "operation": operation},
            cause=error,
        )
