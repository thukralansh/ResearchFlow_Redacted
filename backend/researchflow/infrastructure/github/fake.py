"""Deterministic fixture-backed GitHub adapter."""

from collections.abc import Mapping, Sequence

from researchflow.ports.github import (
    GitHubFile,
    GitHubRepository,
    GitHubResolutionError,
    GitHubTree,
    GitHubTreeEntry,
    ResolvedGitHubRepository,
)

type ResolvedRefKey = tuple[str, str | None]
type RepositoryVersionKey = tuple[str, str, str]
type RepositoryFileKey = tuple[str, str, str, str]


class FakeGitHubProvider:
    """Return bounded commit-pinned repository fixtures."""

    def __init__(
        self,
        *,
        resolved_refs: Mapping[ResolvedRefKey, ResolvedGitHubRepository],
        trees: Mapping[RepositoryVersionKey, GitHubTree | Sequence[GitHubTreeEntry]],
        files: Mapping[RepositoryFileKey, bytes],
        tree_errors: Mapping[RepositoryVersionKey, GitHubResolutionError] | None = None,
        file_errors: Mapping[RepositoryFileKey, GitHubResolutionError] | None = None,
    ) -> None:
        self._resolved_refs = dict(resolved_refs)
        self._trees = {
            key: value if isinstance(value, GitHubTree) else GitHubTree(tuple(value), False)
            for key, value in trees.items()
        }
        self._files = {key: bytes(content) for key, content in files.items()}
        self._tree_errors = dict(tree_errors or {})
        self._file_errors = dict(file_errors or {})

    async def resolve(
        self,
        *,
        repository_url: str,
        requested_ref: str | None,
    ) -> ResolvedGitHubRepository:
        try:
            return self._resolved_refs[(repository_url, requested_ref)]
        except KeyError as error:
            raise GitHubResolutionError("github_repository_or_ref_not_found") from error

    async def tree(
        self,
        *,
        repository: GitHubRepository,
        commit_sha: str,
    ) -> GitHubTree:
        key = (repository.owner, repository.name, commit_sha)
        if failure := self._tree_errors.get(key):
            raise failure
        try:
            return self._trees[key]
        except KeyError as error:
            raise GitHubResolutionError("github_repository_or_ref_not_found") from error

    async def file(
        self,
        *,
        repository: GitHubRepository,
        commit_sha: str,
        path: str,
    ) -> GitHubFile:
        key = (repository.owner, repository.name, commit_sha, path)
        if failure := self._file_errors.get(key):
            raise failure
        try:
            content = self._files[key]
        except KeyError as error:
            raise GitHubResolutionError("github_repository_or_ref_not_found") from error
        return GitHubFile(
            path=path,
            commit_sha=commit_sha,
            content=bytes(content),
            response_bytes=len(content),
        )
