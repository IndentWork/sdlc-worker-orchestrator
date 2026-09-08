"""
GitHub service — all GitHub API interactions for the orchestrator worker.

Usage:
    github = await GitHub.create(github_org, app_id, private_key)
    issue  = await github.get_issue(issue_repo, issue_number)

Each pipeline run creates its own GitHub instance with its own token.
The token is fetched once at creation and reused for all API calls.

Authentication flow:
  private_key (PEM from Key Vault)
      ↓
  _build_jwt()                  → JWT (valid 10 minutes)
      ↓
  _get_org_installation_id()    → installation_id (looked up from org name)
      ↓
  _get_installation_token()     → token (valid 1 hour)
      ↓
  get_issue() / future methods  → API calls using token
"""
import time

import httpx
import jwt as pyjwt

GITHUB_API = "https://api.github.com"


def _build_jwt(app_id: str, private_key: str) -> str:
    """
    Build a signed JWT for GitHub App authentication.
    Valid for 10 minutes — enough to exchange for an installation token.
    """
    now = int(time.time())
    payload = {
        "iat": now - 60,        # issued 60s in past to allow clock skew
        "exp": now + (10 * 60), # expires in 10 minutes
        "iss": app_id,
    }
    return pyjwt.encode(payload, private_key, algorithm="RS256")


async def _get_org_installation_id(github_org: str, jwt_token: str) -> str:
    """Look up the GitHub App installation ID for a given org."""
    url = f"{GITHUB_API}/orgs/{github_org}/installation"
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept":        "application/vnd.github+json",
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return str(response.json()["id"])


async def _get_installation_token(installation_id: str, jwt_token: str) -> str:
    """Exchange JWT for a short-lived installation token (valid 1 hour)."""
    url = f"{GITHUB_API}/app/installations/{installation_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept":        "application/vnd.github+json",
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers)
        response.raise_for_status()
        return response.json()["token"]


class GitHub:
    """
    GitHub API client for one pipeline run.

    Each issue processed gets its own GitHub instance — token fetched
    once at creation and reused for all API calls within that run.
    Do not share instances across pipeline runs.
    """

    def __init__(self, github_org: str, token: str) -> None:
        self._org   = github_org
        self._token = token
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept":        "application/vnd.github+json",
        }

    @classmethod
    async def create(cls, github_org: str, app_id: str, private_key: str) -> "GitHub":
        """
        Build a GitHub client for a given org.
        Fetches an installation token using the App private key.
        """
        jwt_token       = _build_jwt(app_id, private_key)
        installation_id = await _get_org_installation_id(github_org, jwt_token)
        token           = await _get_installation_token(installation_id, jwt_token)
        return cls(github_org, token)

    async def get_file_content(self, repo: str, file_path: str) -> str:
        """
        Fetch the raw content of a file from GitHub.
        Used by the read_file tool so agents can inspect source code.
        """
        import base64
        url = f"{GITHUB_API}/repos/{self._org}/{repo}/contents/{file_path}"

        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self._headers)
            response.raise_for_status()
            encoded = response.json()["content"]

        return base64.b64decode(encoded.replace("\n", "")).decode("utf-8")

    async def get_issue(self, issue_repo: str, issue_number: int) -> dict:
        """
        Fetch issue details — title, body (the requirement), labels.
        The body is the user's plain-English requirement for the agent.
        """
        url = f"{GITHUB_API}/repos/{self._org}/{issue_repo}/issues/{issue_number}"

        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self._headers)
            response.raise_for_status()
            data = response.json()

        return {
            "number": data["number"],
            "title":  data["title"],
            "body":   data["body"] or "",
            "labels": [label["name"] for label in data.get("labels", [])],
            "url":    data["html_url"],
        }
