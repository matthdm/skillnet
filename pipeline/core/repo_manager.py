# CODEX TASK C3 â€” implement this file exactly as specified
# GitHub REST API wrapper. Use requests only â€” no gitpython or subprocess git.
# Token from env var GITHUB_TOKEN. See DESIGN.md Section 11 for method specs.
# All methods must have full type hints including return types.

from __future__ import annotations

import base64
import os
from typing import Any

import requests


class RepoManager:
    def __init__(self) -> None:
        self.token = os.environ["GITHUB_TOKEN"]
        self.owner = os.environ["GITHUB_OWNER"]
        self._base = "https://api.github.com"
        self._headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "skillnet-pipeline/1.0",
        }

    def create_repo(self, name: str, private: bool = True) -> str:
        response = requests.post(
            f"{self._base}/user/repos",
            headers=self._headers,
            json={"name": name, "private": private, "auto_init": True},
            timeout=30,
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        return str(payload["clone_url"])

    def create_branch(self, repo: str, branch: str, from_branch: str = "main") -> None:
        ref_response = requests.get(
            f"{self._base}/repos/{self.owner}/{repo}/git/ref/heads/{from_branch}",
            headers=self._headers,
            timeout=30,
        )
        ref_response.raise_for_status()
        ref_payload: dict[str, Any] = ref_response.json()
        from_sha = str(ref_payload["object"]["sha"])

        create_response = requests.post(
            f"{self._base}/repos/{self.owner}/{repo}/git/refs",
            headers=self._headers,
            json={"ref": f"refs/heads/{branch}", "sha": from_sha},
            timeout=30,
        )
        create_response.raise_for_status()

    def push_files(self, repo: str, branch: str, files: dict[str, str], commit_msg: str) -> None:
        for path, content in files.items():
            content_url = f"{self._base}/repos/{self.owner}/{repo}/contents/{path}"
            encoded_content = base64.b64encode(content.encode("utf-8")).decode("ascii")

            file_sha: str | None = None
            get_response = requests.get(
                content_url,
                headers=self._headers,
                params={"ref": branch},
                timeout=30,
            )
            if get_response.status_code == 200:
                get_payload: dict[str, Any] = get_response.json()
                file_sha = str(get_payload["sha"])
            elif get_response.status_code != 404:
                get_response.raise_for_status()

            put_payload: dict[str, Any] = {
                "message": commit_msg,
                "content": encoded_content,
                "branch": branch,
            }
            if file_sha is not None:
                put_payload["sha"] = file_sha

            put_response = requests.put(
                content_url,
                headers=self._headers,
                json=put_payload,
                timeout=30,
            )
            put_response.raise_for_status()

    def create_pr(self, repo: str, branch: str, title: str, body: str) -> str:
        response = requests.post(
            f"{self._base}/repos/{self.owner}/{repo}/pulls",
            headers=self._headers,
            json={"title": title, "head": branch, "base": "main", "body": body},
            timeout=30,
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        return str(payload["html_url"])

    def get_file_tree(self, repo: str, branch: str = "main") -> list[str]:
        """Return all blob file paths in the repo using the Git Trees API."""
        ref_resp = requests.get(
            f"{self._base}/repos/{self.owner}/{repo}/git/ref/heads/{branch}",
            headers=self._headers,
            timeout=30,
        )
        ref_resp.raise_for_status()
        tree_sha = str(ref_resp.json()["object"]["sha"])

        tree_resp = requests.get(
            f"{self._base}/repos/{self.owner}/{repo}/git/trees/{tree_sha}",
            headers=self._headers,
            params={"recursive": "1"},
            timeout=30,
        )
        tree_resp.raise_for_status()
        tree_data: dict[str, Any] = tree_resp.json()
        return [item["path"] for item in tree_data.get("tree", []) if item["type"] == "blob"]

    def get_file_content(self, repo: str, path: str, branch: str = "main") -> str:
        """Fetch and decode a single file's content from GitHub."""
        resp = requests.get(
            f"{self._base}/repos/{self.owner}/{repo}/contents/{path}",
            headers=self._headers,
            params={"ref": branch},
            timeout=30,
        )
        resp.raise_for_status()
        payload: dict[str, Any] = resp.json()
        return base64.b64decode(payload["content"]).decode("utf-8")
