"""Bounded GitHub access interface."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable


class GitHubTreeEntryType(StrEnum):
    BLOB = "blob"
    TREE = "tree"
    SYMLINK = "symlink"
    SUBMODULE = "submodule"


@dataclass(frozen=True, slots=True)
class GitHubRepository:
    url: str
    owner: str
    name: str
    default_branch: str | None = None
    description: str | None = None
    is_private: bool = False


@dataclass(frozen=True, slots=True)
class ResolvedGitHubRepository:
    repository: GitHubRepository
    requested_ref: str
    commit_sha: str


class GitHubResolutionError(Exception):
    """Classified GitHub resolution failure without credential or response leakage."""

    def __init__(self, reason: str, *, retry_after_seconds: int | None = None) -> None:
        self.reason = reason
        self.retry_after_seconds = retry_after_seconds
        super().__init__(reason)


@runtime_checkable
class GitHubGateway(Protocol):
    async def resolve(
        self,
        *,
        repository_url: str,
        requested_ref: str | None,
    ) -> ResolvedGitHubRepository: ...


@dataclass(frozen=True, slots=True)
class GitHubTreeEntry:
    path: str
    entry_type: GitHubTreeEntryType
    size_bytes: int | None


@dataclass(frozen=True, slots=True)
class GitHubTree:
    entries: tuple[GitHubTreeEntry, ...]
    truncated: bool
    response_bytes: int = 0


@dataclass(frozen=True, slots=True)
class GitHubFile:
    path: str
    commit_sha: str
    content: bytes
    response_bytes: int = 0


@runtime_checkable
class GitHubProvider(Protocol):
    """Commit-pinned GitHub reads without clone or execution."""

    async def resolve(
        self,
        *,
        repository_url: str,
        requested_ref: str | None,
    ) -> ResolvedGitHubRepository: ...

    async def tree(
        self,
        *,
        repository: GitHubRepository,
        commit_sha: str,
    ) -> GitHubTree: ...

    async def file(
        self,
        *,
        repository: GitHubRepository,
        commit_sha: str,
        path: str,
    ) -> GitHubFile: ...
