"""Generate and guard the disposable full Supabase stack used by tests."""

from __future__ import annotations

import argparse
import os
import shutil
import tomllib
from pathlib import Path
from typing import Final

from alembic import command
from alembic.config import Config
from pydantic import AnyHttpUrl, SecretStr

from researchflow.config import PersistenceEnvironment, Settings
from researchflow.errors import ConfigurationError
from researchflow.infrastructure.database.session import assert_safe_local_test_target

TEST_PROJECT_ID: Final = "researchflow-test"
TEST_STACK_ROOT: Final = Path(__file__).resolve().parents[3] / ".researchflow-test-stack"
TEST_DATABASE_URL: Final = "postgresql+psycopg://postgres:postgres@127.0.0.1:55322/postgres"
TEST_SUPABASE_URL: Final = "http://127.0.0.1:55321"

_EXPECTED_CONFIG: Final = {
    ("", "project_id"): TEST_PROJECT_ID,
    ("api", "port"): 55321,
    ("db", "port"): 55322,
    ("db", "shadow_port"): 55320,
    ("db.pooler", "port"): 55329,
    ("studio", "port"): 55323,
    ("local_smtp", "port"): 55324,
    ("edge_runtime", "inspector_port"): 55883,
    ("analytics", "port"): 55327,
}


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _render_test_config(source: str) -> str:
    """Patch only project identity and listener ports in canonical Supabase config."""
    section = ""
    seen: set[tuple[str, str]] = set()
    lines: list[str] = []
    for line in source.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1]
        replacement = None
        for (target_section, key), value in _EXPECTED_CONFIG.items():
            if section == target_section and stripped.startswith(f"{key} ="):
                replacement = (
                    f'{key} = "{value}"\n' if isinstance(value, str) else f"{key} = {value}\n"
                )
                seen.add((target_section, key))
                break
        lines.append(replacement if replacement is not None else line)
    missing = set(_EXPECTED_CONFIG) - seen
    if missing:
        raise ConfigurationError(
            details={
                "reason": "test_stack_source_config_drift",
                "missing": repr(sorted(missing)),
            }
        )
    return "".join(lines)


def prepare_test_stack(*, workdir: Path = TEST_STACK_ROOT) -> Path:
    """Idempotently derive a disposable test stack without copying runtime state."""
    source_dir = _repository_root() / "supabase"
    target_dir = workdir / "supabase"
    target_dir.mkdir(parents=True, exist_ok=True)
    rendered = _render_test_config((source_dir / "config.toml").read_text(encoding="utf-8"))
    (target_dir / "config.toml").write_text(rendered, encoding="utf-8")
    shutil.copy2(source_dir / "seed.sql", target_dir / "seed.sql")
    assert_disposable_test_stack(workdir=workdir)
    return workdir


def assert_disposable_test_stack(
    *,
    workdir: Path = TEST_STACK_ROOT,
    settings: Settings | None = None,
) -> None:
    """Fail closed unless config, endpoints, mode, and database identities are exact."""
    config_path = workdir / "supabase" / "config.toml"
    if not config_path.is_file():
        raise ConfigurationError(details={"reason": "test_stack_config_missing"})
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    actual = {
        ("", "project_id"): config.get("project_id"),
        ("api", "port"): config.get("api", {}).get("port"),
        ("db", "port"): config.get("db", {}).get("port"),
        ("db", "shadow_port"): config.get("db", {}).get("shadow_port"),
        ("db.pooler", "port"): config.get("db", {}).get("pooler", {}).get("port"),
        ("studio", "port"): config.get("studio", {}).get("port"),
        ("local_smtp", "port"): config.get("local_smtp", {}).get("port"),
        ("edge_runtime", "inspector_port"): config.get("edge_runtime", {}).get("inspector_port"),
        ("analytics", "port"): config.get("analytics", {}).get("port"),
    }
    if actual != _EXPECTED_CONFIG:
        raise ConfigurationError(details={"reason": "test_stack_identity_mismatch"})

    effective = settings or Settings(
        persistence_environment=PersistenceEnvironment.LOCAL,
        test_database_url=SecretStr(TEST_DATABASE_URL),
        supabase_url=AnyHttpUrl(TEST_SUPABASE_URL),
    )
    assert_safe_local_test_target(effective)


def migrate_test_stack(*, workdir: Path = TEST_STACK_ROOT) -> None:
    """Upgrade only the positively identified disposable stack primary database."""
    settings = Settings(
        persistence_environment=PersistenceEnvironment.LOCAL,
        test_database_url=SecretStr(TEST_DATABASE_URL),
        supabase_url=AnyHttpUrl(TEST_SUPABASE_URL),
    )
    assert_disposable_test_stack(workdir=workdir, settings=settings)
    prior_test_url = os.environ.get("TEST_DATABASE_URL")
    prior_supabase_url = os.environ.get("SUPABASE_URL")
    prior_environment = os.environ.get("PERSISTENCE_ENVIRONMENT")
    try:
        os.environ["TEST_DATABASE_URL"] = TEST_DATABASE_URL
        os.environ["SUPABASE_URL"] = TEST_SUPABASE_URL
        os.environ["PERSISTENCE_ENVIRONMENT"] = "local"
        config = Config(_repository_root() / "alembic.ini")
        config.attributes["use_test_database"] = True
        command.upgrade(config, "head")
    finally:
        for key, value in (
            ("TEST_DATABASE_URL", prior_test_url),
            ("SUPABASE_URL", prior_supabase_url),
            ("PERSISTENCE_ENVIRONMENT", prior_environment),
        ):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "guard", "migrate"))
    args = parser.parse_args()
    if args.action == "prepare":
        prepare_test_stack(workdir=TEST_STACK_ROOT)
    elif args.action == "guard":
        assert_disposable_test_stack(workdir=TEST_STACK_ROOT)
    else:
        migrate_test_stack(workdir=TEST_STACK_ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
