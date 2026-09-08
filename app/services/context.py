"""
Pipeline context — everything the pipeline needs, loaded once at the start.

Encapsulates the data loading and validation that used to clutter orchestrator.py.
Raises ContextError with a clear reason if any step fails.
"""
import json
import logging
from dataclasses import dataclass

from app.services.github import GitHub
from app.services.storage import (
    find_project_for_issue_repo,
    get_repos_for_project,
    load_sdlc_config,
)

log = logging.getLogger("worker.orchestrator")


class ContextError(Exception):
    """Raised when pipeline context cannot be built. Message describes the reason."""


@dataclass
class PipelineContext:
    """Everything needed to run one pipeline for one issue."""
    resource_code: str
    tier:          str
    github_org:    str
    issue_repo:    str
    issue_number:  int
    project_name:  str
    repos:         list[str]     # code repos to search (e.g. ['cart-service', 'order-service'])
    requirement:   str           # the issue body text
    issue_title:   str
    issue_url:     str


def load_context(payload: dict, github: GitHub) -> PipelineContext:
    """
    Build the pipeline context from a Service Bus message + GitHub client.

    Steps:
      1. Read sdlc.yml from Storage
      2. Find which project the issue_repo belongs to
      3. Fetch the issue body from GitHub

    Raises ContextError if any step fails or the requirement is empty.
    """
    resource_code = payload["resource_code"]
    tier          = payload["tier"]
    github_org    = payload["github_org"]
    issue_repo    = payload["issue_repo"]
    issue_number  = payload["issue_number"]

    # Load sdlc.yml
    try:
        config = load_sdlc_config(tier, resource_code, github_org)
    except Exception as exc:
        raise ContextError(f"could not read sdlc.yml from Storage: {exc}")

    log.info(json.dumps({"event": "sdlc_config_loaded", "github_org": github_org}))

    # Find the project for this issue_repo
    project = find_project_for_issue_repo(config, issue_repo)
    if not project:
        raise ContextError(f"issue_repo '{issue_repo}' not listed in any project in sdlc.yml")

    repos = get_repos_for_project(project)
    log.info(json.dumps({
        "event":   "project_resolved",
        "project": project["name"],
        "repos":   repos,
    }))

    # Fetch the issue
    try:
        issue = github.get_issue(issue_repo, issue_number)
    except Exception as exc:
        raise ContextError(f"could not fetch issue #{issue_number} from {issue_repo}: {exc}")

    log.info(json.dumps({
        "event":  "issue_fetched",
        "number": issue["number"],
        "title":  issue["title"],
    }))

    if not issue["body"].strip():
        raise ContextError("issue body is empty — tenant must describe the requirement in the issue body")

    return PipelineContext(
        resource_code = resource_code,
        tier          = tier,
        github_org    = github_org,
        issue_repo    = issue_repo,
        issue_number  = issue_number,
        project_name  = project["name"],
        repos         = repos,
        requirement   = issue["body"],
        issue_title   = issue["title"],
        issue_url     = issue["url"],
    )
