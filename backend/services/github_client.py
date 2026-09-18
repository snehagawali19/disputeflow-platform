"""Read-only GitHub Contents API client. Never logs tokens or executes repository files."""

from __future__ import annotations

import base64
from typing import Any

import httpx

from backend.core.config import Settings


class GitHubAuthError(Exception):
    pass


class GitHubAPIError(Exception):
    pass


class GitHubClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self._settings = settings
        owner, repo = settings.github_owner_repo
        self.owner = owner
        self.repo = repo
        self.ref = settings.github_ref or "main"
        self.input_path = settings.github_input_path.strip("/")
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(timeout=30.0)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "DisputeFlow-github-source",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = (self._settings.github_token or "").strip()
        if token and not token.startswith("your_"):
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def aclose(self) -> None:
        if self._owned_client:
            await self._client.aclose()

    async def _get(self, url: str, params: dict[str, str] | None = None) -> httpx.Response:
        try:
            response = await self._client.get(url, headers=self._headers(), params=params)
        except httpx.RequestError as exc:
            raise GitHubAPIError(
                "Could not reach GitHub. Check the network connection and try again."
            ) from exc
        if response.status_code in {401, 403}:
            raise GitHubAuthError("GitHub authentication failed")
        if response.status_code == 404:
            target = url.split("repos/", 1)[-1]
            raise GitHubAPIError(
                f"GitHub 404 for {target}. Set GITHUB_OWNER and GITHUB_REPO to a real "
                "repository that contains disputes/input (example: snehagawali19/disputeflow-cases)."
            )
        if response.status_code >= 400:
            raise GitHubAPIError(f"GitHub API error {response.status_code}")
        return response

    async def get_ref_commit(self, ref: str | None = None) -> str:
        branch = ref or self.ref
        url = f"https://api.github.com/repos/{self.owner}/{self.repo}/commits/{branch}"
        payload = (await self._get(url)).json()
        return str(payload["sha"])

    async def list_input_files(self, commit_sha: str) -> list[dict[str, Any]]:
        url = f"https://api.github.com/repos/{self.owner}/{self.repo}/contents/{self.input_path}"
        payload = (await self._get(url, params={"ref": commit_sha})).json()
        if not isinstance(payload, list):
            raise GitHubAPIError("GitHub input path is not a directory")
        files = []
        for item in payload:
            if item.get("type") != "file":
                continue
            name = str(item.get("name") or "")
            if not name.endswith(".json"):
                continue
            files.append(
                {
                    "path": str(item["path"]),
                    "sha": str(item["sha"]),
                    "name": name,
                }
            )
        return sorted(files, key=lambda item: item["path"])

    async def get_file(self, path: str, commit_sha: str) -> tuple[bytes, str]:
        url = f"https://api.github.com/repos/{self.owner}/{self.repo}/contents/{path}"
        payload = (await self._get(url, params={"ref": commit_sha})).json()
        if payload.get("encoding") != "base64":
            raise GitHubAPIError("Unexpected GitHub file encoding")
        content = base64.b64decode(payload["content"])
        return content, str(payload.get("sha") or "")
