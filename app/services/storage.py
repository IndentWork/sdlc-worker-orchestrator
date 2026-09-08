"""
Storage service — reads sdlc.yml from Azure Blob Storage.

Path: sdlc/{resource_code}/{github_org}/sdlc.yml

sdlc.yml tells us:
  - Which projects the tenant has
  - Which repos belong to each project
  - Which issue_repo maps to which project

Used by the orchestrator to find which repos are relevant
for an issue based on which issue repo it came from.
"""
import os

import yaml
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

CONTAINER = "sdlc"


def _account_url(tier: str, resource_code: str) -> str:
    """Derive Storage account URL from tenant tier and resource_code."""
    env = os.environ.get("ENV", "dev")
    if tier == "shared":
        name = f"stsdlcshared{env}"
    else:
        name = f"stsdlc{resource_code}{env}"
    return f"https://{name}.blob.core.windows.net"


def load_sdlc_config(tier: str, resource_code: str, github_org: str) -> dict:
    """
    Read and parse sdlc.yml from Storage.
    Returns the parsed YAML as a dict.

    Blob path: sdlc/{resource_code}/{github_org}/sdlc.yml
    """
    url       = _account_url(tier, resource_code)
    blob_path = f"{resource_code}/{github_org}/sdlc.yml"

    client = BlobServiceClient(url, DefaultAzureCredential())
    blob   = client.get_blob_client(CONTAINER, blob_path)
    data   = blob.download_blob().readall()

    return yaml.safe_load(data)


def find_project_for_issue_repo(config: dict, issue_repo: str) -> dict | None:
    """
    Find which project an issue repo belongs to.

    Looks through sdlc.yml projects for a matching issue_repo URL.
    Returns the project dict or None if not found.

    Example sdlc.yml:
      projects:
        - name: ecommerce
          issue_repo: https://github.com/sdlc-tenant/ecommerce-issues
          repos:
            - name: cart-service
            - name: order-service
    """
    for project in config.get("projects", []):
        issue_repo_url = project.get("issue_repo", "")
        # issue_repo_url is full URL, issue_repo is just the repo name
        if issue_repo_url.rstrip("/").endswith(f"/{issue_repo}"):
            return project
    return None


def get_repos_for_project(project: dict) -> list[str]:
    """
    Extract repo names from a project dict.
    Returns list of repo names e.g. ['cart-service', 'order-service']
    """
    return [repo["name"] for repo in project.get("repos", [])]
