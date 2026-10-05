"""Explicit, redacted Supabase database and Storage smoke command."""

import asyncio

from researchflow.config import get_settings
from researchflow.infrastructure.database.health import check_database_health
from researchflow.infrastructure.database.session import (
    assert_safe_hosted_development_target,
    create_database_engine,
)
from researchflow.infrastructure.storage.supabase import SupabaseStorageProvider

_SMOKE_KEY = "smoke/s03-connectivity.bin"
_SMOKE_CONTENT = b"researchflow-s03-storage-smoke"


async def run() -> None:
    settings = get_settings()
    assert_safe_hosted_development_target(settings)

    engine = create_database_engine(settings)
    try:
        health = await check_database_health(engine)
    finally:
        await engine.dispose()

    storage = SupabaseStorageProvider(
        supabase_url=str(settings.supabase_url),
        service_role_key=settings.supabase_service_role_key.get_secret_value(),
        bucket_name=settings.supabase_storage_bucket,
        max_upload_bytes=settings.max_upload_bytes,
    )
    uploaded = False
    try:
        await storage.ensure_private_bucket()
        stored = await storage.put(
            object_key=_SMOKE_KEY,
            content=_SMOKE_CONTENT,
            content_type="application/octet-stream",
        )
        uploaded = True
        actual = await storage.read(object_key=_SMOKE_KEY)
        if actual != _SMOKE_CONTENT or stored.size_bytes != len(_SMOKE_CONTENT):
            raise RuntimeError("Storage smoke content mismatch")
    finally:
        try:
            if uploaded:
                await storage.delete(object_key=_SMOKE_KEY)
        finally:
            await storage.close()

    print("hosted database smoke: passed")
    print(f"hosted database timezone: {health.timezone}")
    print("hosted private Storage round trip: passed and cleaned up")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
