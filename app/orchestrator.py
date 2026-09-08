"""
Orchestrator — pure Python coordinator. No LLM here.

Sequence:
  1. Load sdlc.yml from Storage
  2. Find which project the issue belongs to (via issue_repo)
  3. Get repos for that project
  4. Fetch issue from GitHub (requirement = issue body)
  5. Create tools filtered to those repos only
  6. Run Analyst → get {status, repo, files_to_change, ...}
  7. If not feasible or already implemented → stop
  8. If feasible → continue to Coder (next step)
"""
import json
import logging

from app.agents.analyst import run_analyst
from app.services.github import GitHub
from app.services.storage import (
    find_project_for_issue_repo,
    get_repos_for_project,
    load_sdlc_config,
)
from app.tools.codebase import make_analyst_tools

log = logging.getLogger("worker.orchestrator")


def run(payload: dict, github: GitHub, openai_api_key: str) -> None:
    """
    Run the full orchestration pipeline for one issue (sync).

    payload        — {resource_code, github_org, tier, issue_repo, issue_number}
    github         — GitHub client (already authenticated)
    openai_api_key — OpenAI key from Key Vault
    """
    resource_code = payload["resource_code"]
    tier          = payload["tier"]
    github_org    = payload["github_org"]
    issue_repo    = payload["issue_repo"]
    issue_number  = payload["issue_number"]

    # Step 1 — load sdlc.yml from Storage
    try:
        config = load_sdlc_config(tier, resource_code, github_org)
        log.info(json.dumps({"event": "sdlc_config_loaded", "github_org": github_org}))
    except Exception as exc:
        log.error(json.dumps({
            "event":  "sdlc_config_failed",
            "error":  str(exc),
            "reason": "could not read sdlc.yml from Storage — was it uploaded?",
        }))
        return

    # Step 2 — find which project this issue repo belongs to
    project = find_project_for_issue_repo(config, issue_repo)
    if not project:
        log.error(json.dumps({
            "event":      "project_not_found",
            "issue_repo": issue_repo,
            "reason":     "issue_repo not listed in any project in sdlc.yml",
        }))
        return

    # Step 3 — get repos for that project
    repos = get_repos_for_project(project)
    log.info(json.dumps({
        "event":   "project_resolved",
        "project": project["name"],
        "repos":   repos,
    }))

    # Step 4 — fetch issue from GitHub
    try:
        issue       = github.get_issue(issue_repo, issue_number)
        requirement = issue["body"]
        log.info(json.dumps({
            "event":       "issue_fetched",
            "number":      issue["number"],
            "title":       issue["title"],
            "requirement": requirement[:200],
        }))
    except Exception as exc:
        log.error(json.dumps({
            "event":        "issue_fetch_failed",
            "error":        str(exc),
            "issue_number": issue_number,
            "issue_repo":   issue_repo,
        }))
        return

    if not requirement.strip():
        log.error(json.dumps({
            "event":  "empty_requirement",
            "reason": "issue body is empty — tenant must describe the requirement in the issue body",
        }))
        return

    # Step 5 — create tools with sync progress callback
    def on_progress(message: str) -> None:
        """Post a progress update as a comment on the issue."""
        try:
            github.comment_on_issue(issue_repo, issue_number, message)
        except Exception as exc:
            log.warning(json.dumps({"event": "progress_comment_failed", "error": str(exc)}))

    tools = make_analyst_tools(resource_code, repos, on_progress=on_progress)
    log.info(json.dumps({
        "event": "analyst_tools_created",
        "tools": [t.name for t in tools],
        "repos": repos,
    }))

    # post initial comment so tenant knows work has started
    on_progress(f"🤖 SDLC Agent started investigating...\n\nRequirement: {requirement[:300]}")

    # Step 6 — run Analyst
    try:
        analysis = run_analyst(requirement, tools, openai_api_key)
        status   = analysis.get("status")
    except Exception as exc:
        log.error(json.dumps({"event": "analyst_failed", "error": str(exc)}))
        return

    # Step 7 — handle non-feasible outcomes
    if status == "not_feasible":
        log.info(json.dumps({"event": "not_feasible", "summary": analysis.get("summary")}))
        on_progress(f"❌ Not feasible\n\n{analysis.get('summary')}")
        return

    if status == "already_implemented":
        log.info(json.dumps({"event": "already_implemented", "summary": analysis.get("summary")}))
        on_progress(f"✅ Already implemented\n\n{analysis.get('summary')}")
        return

    # Step 8 — feasible
    files = ", ".join(analysis.get("files_to_change", []))
    log.info(json.dumps({
        "event":           "feasible",
        "repo":            analysis.get("repo"),
        "files_to_change": analysis.get("files_to_change"),
        "branch_type":     analysis.get("branch_type"),
        "branch_title":    analysis.get("branch_title"),
        "summary":         analysis.get("summary"),
    }))

    on_progress(
        f"✅ Analysis complete — Feasible\n\n"
        f"**Repo:** {analysis.get('repo')}\n"
        f"**Files to change:** {files}\n"
        f"**Plan:** {analysis.get('summary')}\n\n"
        f"⏳ Handing off to Coder agent..."
    )

    # TODO: create branch → run Coder → create PR → run Reviewer → comment on issue
