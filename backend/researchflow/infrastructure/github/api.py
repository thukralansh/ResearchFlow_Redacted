"""Bounded GitHub REST adapter for public commit, tree, and blob reads."""

import base64
import binascii
from typing import Any
from urllib.parse import quote

import httpx

from researchflow.domain.repository_authority import (
    normalize_github_url,
    validate_commit_sha,
    validate_requested_ref,
)
from researchflow.ports.github import (
    GitHubFile,
    GitHubRepository,
    GitHubResolutionError,
    GitHubTree,
    GitHubTreeEntry,
    GitHubTreeEntryType,
    ResolvedGitHubRepository,
)


class GitHubApiGateway:
    def __init__(
        self,
        *,
        token: str = "",
        timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ResearchFlow/0.1",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = client or httpx.AsyncClient(
            base_url="https://api.github.com",
            headers=headers,
            timeout=timeout_seconds,
            follow_redirects=False,
        )
        self._owns_client = client is None

    async def resolve(
        self,
        *,
        repository_url: str,
        requested_ref: str | None,
    ) -> ResolvedGitHubRepository:
        identity = normalize_github_url(repository_url)
        requested_ref = validate_requested_ref(requested_ref)
        metadata = await self._get(f"/repos/{identity.owner}/{identity.name}")
        private = metadata.get("private")
        default_branch = metadata.get("default_branch")
        if private is not False:
            raise GitHubResolutionError("github_repository_private")
        if not isinstance(default_branch, str) or not default_branch.strip():
            raise GitHubResolutionError("github_repository_empty")
        description = metadata.get("description")
        if description is not None and not isinstance(description, str):
            description = None
        ref = requested_ref or default_branch
        encoded_ref = quote(ref, safe="")
        commit = await self._get(f"/repos/{identity.owner}/{identity.name}/commits/{encoded_ref}")
        commit_sha = commit.get("sha")
        if not isinstance(commit_sha, str):
            raise GitHubResolutionError("github_response_invalid")
        return ResolvedGitHubRepository(
            repository=GitHubRepository(
                url=identity.url,
                owner=identity.owner,
                name=identity.name,
                default_branch=default_branch,
                description=description,
                is_private=False,
            ),
            requested_ref=ref,
            commit_sha=validate_commit_sha(commit_sha),
        )

    async def tree(
        self,
        *,
        repository: GitHubRepository,
        commit_sha: str,
    ) -> GitHubTree:
        commit_sha = validate_commit_sha(commit_sha)
        payload, response_bytes = await self._get_with_size(
            f"/repos/{repository.owner}/{repository.name}/git/trees/{commit_sha}",
            params={"recursive": "1"},
        )
        raw_entries = payload.get("tree")
        truncated = payload.get("truncated")
        if not isinstance(raw_entries, list) or not isinstance(truncated, bool):
            raise GitHubResolutionError("github_response_invalid")
        entries: list[GitHubTreeEntry] = []
        for raw_entry in raw_entries:
            if not isinstance(raw_entry, dict):
                raise GitHubResolutionError("github_response_invalid")
            path = raw_entry.get("path")
            raw_type = raw_entry.get("type")
            mode = raw_entry.get("mode")
            size = raw_entry.get("size")
            if not isinstance(path, str) or not path or not isinstance(raw_type, str):
                raise GitHubResolutionError("github_response_invalid")
            entry_type = _tree_entry_type(raw_type, mode)
            if size is not None and (not isinstance(size, int) or size < 0):
                raise GitHubResolutionError("github_response_invalid")
            entries.append(GitHubTreeEntry(path, entry_type, size))
        return GitHubTree(tuple(entries), truncated, response_bytes)

    async def file(
        self,
        *,
        repository: GitHubRepository,
        commit_sha: str,
        path: str,
    ) -> GitHubFile:
        commit_sha = validate_commit_sha(commit_sha)
        encoded_path = quote(path, safe="/")
        payload, response_bytes = await self._get_with_size(
            f"/repos/{repository.owner}/{repository.name}/contents/{encoded_path}",
            params={"ref": commit_sha},
        )
        content = payload.get("content")
        encoding = payload.get("encoding")
        if payload.get("type") != "file" or not isinstance(content, str) or encoding != "base64":
            raise GitHubResolutionError("github_response_invalid")
        try:
            decoded = base64.b64decode("".join(content.split()), validate=True)
        except (ValueError, binascii.Error) as error:
            raise GitHubResolutionError("github_response_invalid") from error
        return GitHubFile(path, commit_sha, decoded, response_bytes)

    async def _get(self, path: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
        payload, _ = await self._get_with_size(path, params=params)
        return payload

    async def _get_with_size(
        self, path: str, *, params: dict[str, str] | None = None
    ) -> tuple[dict[str, Any], int]:
        try:
            response = await self._client.get(path, params=params)
        except (httpx.TimeoutException, httpx.NetworkError) as error:
            raise GitHubResolutionError("github_unavailable") from error
        if response.is_redirect:
            raise GitHubResolutionError("github_redirect_rejected")
        if response.status_code == 404:
            raise GitHubResolutionError("github_repository_or_ref_not_found")
        if response.status_code == 409:
            raise GitHubResolutionError("github_repository_empty")
        if response.status_code == 401:
            raise GitHubResolutionError("github_authentication_failed")
        if response.status_code in {403, 429}:
            retry_after = _retry_after(response)
            raise GitHubResolutionError("github_rate_limited", retry_after_seconds=retry_after)
        if response.status_code != 200:
            raise GitHubResolutionError("github_unavailable")
        try:
            payload = response.json()
        except ValueError as error:
            raise GitHubResolutionError("github_response_invalid") from error
        if not isinstance(payload, dict):
            raise GitHubResolutionError("github_response_invalid")
        return payload, len(response.content)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _retry_after(response: httpx.Response) -> int | None:
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        seconds = int(value)
    except ValueError:
        return None
    return max(seconds, 0)


def _tree_entry_type(raw_type: str, mode: object) -> GitHubTreeEntryType:
    if raw_type == "tree":
        return GitHubTreeEntryType.TREE
    if raw_type == "commit":
        return GitHubTreeEntryType.SUBMODULE
    if raw_type == "blob" and mode == "120000":
        return GitHubTreeEntryType.SYMLINK
    if raw_type == "blob":
        return GitHubTreeEntryType.BLOB
    raise GitHubResolutionError("github_response_invalid")
