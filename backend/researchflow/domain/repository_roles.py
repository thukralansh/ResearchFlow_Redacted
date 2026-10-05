"""Path-only Repository Stage A classification and bounded coverage policy."""

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Final, Protocol

from researchflow.errors import DomainValidationError


class _EntryType(Protocol):
    @property
    def value(self) -> str: ...


class RepositoryTreeEntryLike(Protocol):
    @property
    def path(self) -> str: ...

    @property
    def entry_type(self) -> _EntryType: ...

    @property
    def size_bytes(self) -> int | None: ...


DISCOVERY_VERSION: Final = "rf-structural-discovery-v1"
DEFAULT_TREE_ENTRY_LIMIT: Final = 5_000
DEFAULT_STRUCTURAL_FILE_LIMIT: Final = 20
DEFAULT_MAX_FILE_BYTES: Final = 256 * 1024

_EXCLUDED_DIRECTORIES: Final = frozenset(
    {
        ".cache",
        ".mypy_cache",
        ".next",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "generated",
        "node_modules",
        "target",
        "third_party",
        "vendor",
        "venv",
    }
)
_BINARY_EXTENSIONS: Final = frozenset(
    {
        ".7z",
        ".a",
        ".avi",
        ".bin",
        ".bmp",
        ".class",
        ".dll",
        ".dylib",
        ".exe",
        ".gif",
        ".gz",
        ".h5",
        ".hdf5",
        ".ico",
        ".jar",
        ".jpeg",
        ".jpg",
        ".mp3",
        ".mp4",
        ".npy",
        ".npz",
        ".o",
        ".onnx",
        ".parquet",
        ".pdf",
        ".pickle",
        ".pkl",
        ".png",
        ".pyc",
        ".so",
        ".tar",
        ".tiff",
        ".wav",
        ".webp",
        ".whl",
        ".zip",
    }
)
_LANGUAGES: Final = {
    ".c": "C",
    ".cc": "C++",
    ".cpp": "C++",
    ".cu": "CUDA",
    ".go": "Go",
    ".h": "C",
    ".hpp": "C++",
    ".java": "Java",
    ".jl": "Julia",
    ".js": "JavaScript",
    ".json": "JSON",
    ".md": "Markdown",
    ".m": "MATLAB",
    ".py": "Python",
    ".r": "R",
    ".rs": "Rust",
    ".sh": "Shell",
    ".toml": "TOML",
    ".ts": "TypeScript",
    ".yaml": "YAML",
    ".yml": "YAML",
}
_MANIFEST_NAMES: Final = frozenset(
    {
        "cargo.lock",
        "cargo.toml",
        "environment.yml",
        "environment.yaml",
        "go.mod",
        "go.sum",
        "package-lock.json",
        "package.json",
        "pipfile",
        "pipfile.lock",
        "poetry.lock",
        "pyproject.toml",
        "setup.cfg",
        "setup.py",
        "uv.lock",
        "yarn.lock",
    }
)
_ENVIRONMENT_NAMES: Final = frozenset(
    {"docker-compose.yml", "docker-compose.yaml", "dockerfile", "makefile", "tox.ini"}
)
_CONFIG_EXTENSIONS: Final = frozenset({".cfg", ".ini", ".json", ".toml", ".yaml", ".yml"})
_STRUCTURAL_ROLES: Final = frozenset(
    {"readme", "dependency_manifest", "environment", "configuration", "entry_point"}
)
_ROLE_PRIORITY: Final = {
    "readme": 0,
    "dependency_manifest": 1,
    "environment": 2,
    "entry_point": 3,
    "configuration": 4,
}


@dataclass(frozen=True, slots=True)
class DiscoveryLimits:
    tree_entry_limit: int = DEFAULT_TREE_ENTRY_LIMIT
    structural_file_limit: int = DEFAULT_STRUCTURAL_FILE_LIMIT
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES

    def validate(self) -> "DiscoveryLimits":
        if min(self.tree_entry_limit, self.structural_file_limit, self.max_file_bytes) <= 0:
            raise DomainValidationError(details={"reason": "repository_discovery_limits_invalid"})
        return self

    def identity(self) -> dict[str, int]:
        return {
            "tree_entry_limit": self.tree_entry_limit,
            "structural_file_limit": self.structural_file_limit,
            "max_file_bytes": self.max_file_bytes,
        }

    def fingerprint(self) -> str:
        encoded = json.dumps(self.identity(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ClassifiedRepositoryEntry:
    path: str
    entry_type: str
    size_bytes: int | None
    language: str | None
    coarse_role: str
    excluded_reason: str | None
    selected: bool = False


@dataclass(frozen=True, slots=True)
class StructuralInventory:
    entries: tuple[ClassifiedRepositoryEntry, ...]
    selected_paths: tuple[str, ...]
    coverage_status: str


@dataclass(frozen=True, slots=True)
class StructuralDocument:
    path: str
    component_type: str
    language: str | None
    content_excerpt: str
    content_hash: str
    line_start: int | None
    line_end: int | None
    response_bytes: int


def classify_repository_tree(
    entries: tuple[RepositoryTreeEntryLike, ...], limits: DiscoveryLimits
) -> StructuralInventory:
    limits.validate()
    classified: list[ClassifiedRepositoryEntry] = []
    seen_casefolded: set[str] = set()
    for entry in sorted(entries, key=lambda item: (item.path.casefold(), item.path)):
        normalized_path = entry.path.strip("/")
        path = PurePosixPath(normalized_path)
        parts = path.parts
        suffix = path.suffix.casefold()
        excluded: str | None = None
        if not normalized_path or any(part in {"", ".", ".."} for part in parts):
            excluded = "invalid_path"
        elif normalized_path.casefold() in seen_casefolded:
            excluded = "duplicate_case_insensitive_path"
        elif entry.entry_type.value in {"symlink", "submodule"}:
            excluded = f"unsupported_{entry.entry_type.value}"
        elif any(part.casefold() in _EXCLUDED_DIRECTORIES for part in parts[:-1]):
            excluded = "excluded_directory"
        elif suffix in _BINARY_EXTENSIONS:
            excluded = "binary_extension"
        elif entry.entry_type.value == "blob" and (
            entry.size_bytes is None or entry.size_bytes > limits.max_file_bytes
        ):
            excluded = "size_unknown" if entry.size_bytes is None else "oversized_file"
        seen_casefolded.add(normalized_path.casefold())
        language = _LANGUAGES.get(suffix)
        role = _coarse_role(path, entry.entry_type, language)
        classified.append(
            ClassifiedRepositoryEntry(
                path=normalized_path,
                entry_type=entry.entry_type.value,
                size_bytes=entry.size_bytes,
                language=language,
                coarse_role=role,
                excluded_reason=excluded,
            )
        )

    eligible = sorted(
        (
            item
            for item in classified
            if item.entry_type == "blob"
            and item.excluded_reason is None
            and item.coarse_role in _STRUCTURAL_ROLES
        ),
        key=lambda item: (
            _ROLE_PRIORITY[item.coarse_role],
            len(PurePosixPath(item.path).parts),
            item.path.casefold(),
            item.path,
        ),
    )
    selected_paths = {item.path for item in eligible[: limits.structural_file_limit]}
    limited_paths = {item.path for item in eligible[limits.structural_file_limit :]}
    updated = tuple(
        replace(
            item,
            selected=item.path in selected_paths,
            excluded_reason=(
                "structural_file_limit" if item.path in limited_paths else item.excluded_reason
            ),
        )
        for item in classified
    )
    return StructuralInventory(
        entries=updated,
        selected_paths=tuple(item.path for item in eligible if item.path in selected_paths),
        coverage_status="partial" if limited_paths else "complete",
    )


def replace_entry_exclusion(
    entries: tuple[ClassifiedRepositoryEntry, ...], *, path: str, reason: str
) -> tuple[ClassifiedRepositoryEntry, ...]:
    return tuple(
        replace(item, selected=False, excluded_reason=reason) if item.path == path else item
        for item in entries
    )


def decode_structural_document(
    entry: ClassifiedRepositoryEntry, content: bytes, *, maximum_bytes: int
) -> StructuralDocument:
    if len(content) > maximum_bytes:
        raise DomainValidationError(details={"reason": "repository_file_oversized"})
    if b"\x00" in content:
        raise DomainValidationError(details={"reason": "repository_file_binary"})
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise DomainValidationError(details={"reason": "repository_file_decode_failed"}) from error
    control_count = sum(
        character < " " and character not in {"\n", "\r", "\t"} for character in text
    )
    if text and control_count / len(text) > 0.01:
        raise DomainValidationError(details={"reason": "repository_file_binary"})
    line_end = text.count("\n") + 1 if text else None
    return StructuralDocument(
        path=entry.path,
        component_type=entry.coarse_role,
        language=entry.language,
        content_excerpt=text,
        content_hash=hashlib.sha256(content).hexdigest(),
        line_start=1 if text else None,
        line_end=line_end,
        response_bytes=len(content),
    )


def repository_tree_hash(entries: tuple[ClassifiedRepositoryEntry, ...]) -> str:
    payload = [
        {
            "path": item.path,
            "entry_type": item.entry_type,
            "size_bytes": item.size_bytes,
            "language": item.language,
            "coarse_role": item.coarse_role,
            "excluded_reason": item.excluded_reason,
        }
        for item in entries
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _coarse_role(path: PurePosixPath, entry_type: _EntryType, language: str | None) -> str:
    if entry_type.value == "tree":
        return "directory"
    name = path.name.casefold()
    suffix = path.suffix.casefold()
    if name.startswith("readme"):
        return "readme"
    if name in _MANIFEST_NAMES or name.startswith("requirements"):
        return "dependency_manifest"
    if name in _ENVIRONMENT_NAMES or name.startswith(".env"):
        return "environment"
    if name in {"__main__.py", "app.py", "cli.py", "manage.py"} or name.startswith("main"):
        return "entry_point"
    if suffix in _CONFIG_EXTENSIONS or ".github" in {part.casefold() for part in path.parts}:
        return "configuration"
    if "test" in {part.casefold() for part in path.parts} or name.startswith("test_"):
        return "test"
    if suffix in {".md", ".rst"} or "docs" in {part.casefold() for part in path.parts}:
        return "documentation"
    return "source" if language is not None else "other"
