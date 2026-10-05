"""Bounded structural discovery over one commit-pinned public repository."""

from dataclasses import dataclass

from researchflow.domain.repository_roles import (
    ClassifiedRepositoryEntry,
    DiscoveryLimits,
    StructuralDocument,
    classify_repository_tree,
    decode_structural_document,
    replace_entry_exclusion,
    repository_tree_hash,
)
from researchflow.errors import DomainValidationError
from researchflow.ports.github import (
    GitHubProvider,
    GitHubRepository,
    GitHubResolutionError,
)


class RepositoryDiscoveryUnsupportedError(Exception):
    """Tree shape cannot be represented truthfully inside first-slice limits."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class RepositoryDiscoveryFetchError(GitHubResolutionError):
    """Classified provider failure with truthful attempted-fetch accounting."""

    def __init__(
        self,
        error: GitHubResolutionError,
        *,
        request_count: int,
        response_bytes: int,
        content_bytes: int,
    ) -> None:
        super().__init__(error.reason, retry_after_seconds=error.retry_after_seconds)
        self.request_count = request_count
        self.response_bytes = response_bytes
        self.content_bytes = content_bytes


@dataclass(frozen=True, slots=True)
class StructuralDiscoveryPayload:
    entries: tuple[ClassifiedRepositoryEntry, ...]
    documents: tuple[StructuralDocument, ...]
    tree_hash: str
    coverage_status: str
    request_count: int
    response_bytes: int
    content_bytes: int


async def discover_repository_structure(
    *,
    github: GitHubProvider,
    repository: GitHubRepository,
    commit_sha: str,
    limits: DiscoveryLimits,
) -> StructuralDiscoveryPayload:
    try:
        tree = await github.tree(repository=repository, commit_sha=commit_sha)
    except GitHubResolutionError as error:
        raise RepositoryDiscoveryFetchError(
            error,
            request_count=1,
            response_bytes=0,
            content_bytes=0,
        ) from error
    if tree.truncated:
        raise RepositoryDiscoveryUnsupportedError("repository_tree_truncated")
    if len(tree.entries) > limits.tree_entry_limit:
        raise RepositoryDiscoveryUnsupportedError("repository_tree_over_limit")

    inventory = classify_repository_tree(tree.entries, limits)
    entries = inventory.entries
    documents: list[StructuralDocument] = []
    request_count = 1
    response_bytes = tree.response_bytes
    content_bytes = 0
    coverage_status = inventory.coverage_status
    by_path = {entry.path: entry for entry in entries}
    for path in inventory.selected_paths:
        entry = by_path[path]
        request_count += 1
        try:
            fetched = await github.file(
                repository=repository,
                commit_sha=commit_sha,
                path=path,
            )
            response_bytes += fetched.response_bytes
            document = decode_structural_document(
                entry, fetched.content, maximum_bytes=limits.max_file_bytes
            )
        except GitHubResolutionError as error:
            if error.reason != "github_repository_or_ref_not_found":
                raise RepositoryDiscoveryFetchError(
                    error,
                    request_count=request_count,
                    response_bytes=response_bytes,
                    content_bytes=content_bytes,
                ) from error
            entries = replace_entry_exclusion(entries, path=path, reason="missing_blob")
            coverage_status = "partial"
            continue
        except DomainValidationError as error:
            reason = str(error.public_details.get("reason", "repository_file_invalid"))
            entries = replace_entry_exclusion(entries, path=path, reason=reason)
            coverage_status = "partial"
            continue
        documents.append(document)
        content_bytes += len(fetched.content)

    return StructuralDiscoveryPayload(
        entries=entries,
        documents=tuple(documents),
        tree_hash=repository_tree_hash(entries),
        coverage_status=coverage_status,
        request_count=request_count,
        response_bytes=response_bytes,
        content_bytes=content_bytes,
    )
