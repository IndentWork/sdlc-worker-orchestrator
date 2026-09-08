"""
Orchestrator — pure Python coordinator. No LLM here.

Reads top-to-bottom as the full pipeline flow:
  1. Load context (sdlc.yml + project + issue)
  2. Start progress tracker
  3. Run Analyst
  4. Branch on analyst result:
     - not_feasible / already_implemented → stop
     - feasible → hand off to Coder (next iteration)
"""
import json
import logging

from app.agents.analyst import run_analyst
from app.observability.progress import ProgressTracker
from app.services.context import ContextError, load_context
from app.services.github import GitHub
from app.tools.codebase import make_analyst_tools

log = logging.getLogger("worker.orchestrator")


def run(payload: dict, github: GitHub, openai_api_key: str) -> None:
    """Run the full orchestration pipeline for one issue."""

    # Step 1 — Load everything the pipeline needs
    try:
        ctx = load_context(payload, github)
    except ContextError as exc:
        log.error(json.dumps({"event": "context_failed", "reason": str(exc)}))
        return

    # Step 2 — Start progress tracker (posts comments on the issue)
    tracker = ProgressTracker(github, ctx.issue_repo, ctx.issue_number)
    tracker.started(ctx.requirement)

    # Step 3 — Analyst investigates the codebase
    tools    = make_analyst_tools(ctx.resource_code, ctx.repos, on_progress=tracker)
    analysis = run_analyst(ctx.requirement, tools, openai_api_key)

    # Step 4 — Branch on analyst result
    status = analysis.get("status")

    if status == "not_feasible":
        tracker.not_feasible(analysis.get("summary", ""))
        return

    if status == "already_implemented":
        tracker.already_implemented(analysis.get("summary", ""))
        return

    # feasible — hand off to Coder
    tracker.feasible(
        repo    = analysis.get("repo"),
        files   = analysis.get("files_to_change", []),
        summary = analysis.get("summary", ""),
    )

    # TODO: coder = run_coder(ctx, analysis, tools, openai_api_key)
    # TODO: reviewer = run_reviewer(ctx, coder, openai_api_key)
    # TODO: tracker.done(reviewer)
