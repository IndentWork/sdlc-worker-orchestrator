"""
Orchestrator — SDLCPipeline class runs the full pipeline for one issue.

Reads top-to-bottom in run():
  1. Analyst investigates the codebase
  2. If not feasible → stop
  3. (future) Coder implements the change
  4. (future) Create PR
  5. (future) Reviewer reviews the PR
  6. (future) Wait for human approval
  7. (future) Merge

State lives in self.* — each step method updates state and progresses the pipeline.
No LLM in this file — agents are called via run_analyst() / run_coder() / run_reviewer().
"""
import logging

from app.agents.analyst import run_analyst
from app.observability.progress import ProgressTracker
from app.services.context import ContextError, load_context
from app.services.github import GitHub
from app.tools.codebase import make_analyst_tools

log = logging.getLogger("worker.orchestrator")


class SDLCPipeline:
    """Runs the full SDLC pipeline for one labeled issue."""

    def __init__(self, payload: dict, github: GitHub, openai_api_key: str) -> None:
        """Load context, set up tracker. No side effects on external systems here."""
        self.payload        = payload
        self.github         = github
        self.openai_api_key = openai_api_key

        # Populated by pipeline steps
        self.ctx      = None   # PipelineContext (sdlc.yml + project + issue)
        self.tracker  = None   # ProgressTracker (posts on issue)
        self.analysis = None   # Analyst output {status, repo, files_to_change, ...}

    # ── Pipeline steps ────────────────────────────────────────────────────────

    def _load_context(self) -> None:
        """Load sdlc.yml, resolve project, fetch issue. Raises ContextError on failure."""
        self.ctx     = load_context(self.payload, self.github)
        self.tracker = ProgressTracker(self.github, self.ctx.issue_repo, self.ctx.issue_number)
        self.tracker.started(self.ctx.requirement)

    def _analyze(self) -> None:
        """Analyst investigates the codebase and returns {status, files_to_change, ...}."""
        tools         = make_analyst_tools(self.ctx.resource_code, self.ctx.repos, on_progress=self.tracker)
        self.analysis = run_analyst(self.ctx.requirement, tools, self.openai_api_key)

    def _report_analysis(self) -> None:
        """Post the analyst's verdict on the issue."""
        status = self.analysis.get("status")

        if status == "not_feasible":
            self.tracker.not_feasible(self.analysis.get("summary", ""))
        elif status == "already_implemented":
            self.tracker.already_implemented(self.analysis.get("summary", ""))
        elif status == "feasible":
            self.tracker.feasible(
                repo    = self.analysis.get("repo"),
                files   = self.analysis.get("files_to_change", []),
                summary = self.analysis.get("summary", ""),
            )

    # TODO: def _create_branch(self)
    # TODO: def _run_coder(self)
    # TODO: def _create_pr(self)
    # TODO: def _run_reviewer(self)
    # TODO: def _wait_for_human(self)
    # TODO: def _merge_and_close(self)

    # ── Orchestration ─────────────────────────────────────────────────────────

    def run(self) -> None:
        """Sequences all pipeline steps. Reads top-to-bottom as the pipeline flow."""

        # Step 1 — Load pipeline context
        # Reads sdlc.yml from Storage, finds the project for this issue_repo,
        # fetches the issue body from GitHub. Also posts the "started" comment.
        try:
            self._load_context()
        except ContextError as exc:
            log.error(f"context_failed: {exc}")
            return

        # Step 2 — Analyst investigates the codebase
        # LLM uses search_code (AI Search) and get_dependencies (Cosmos DB)
        # to find which files need to change. Returns {status, repo, files_to_change, ...}
        self._analyze()

        # Step 3 — Post analyst's verdict as a comment on the issue
        # Tenant sees: "Feasible - here are the files" OR "Not feasible - reason"
        self._report_analysis()

        # Step 4 — Stop early if analyst said not_feasible or already_implemented
        # Only continue to Coder when status is 'feasible'
        if self.analysis.get("status") != "feasible":
            return

        # Step 5 — Coder implements the change (TODO)
        # self._create_branch()      → creates feature branch on GitHub
        # self._run_coder()          → LLM writes code, commits, pushes
        # self._create_pr()          → opens PR from branch → main

        # Step 6 — Reviewer reviews the PR (TODO)
        # self._run_reviewer()       → LLM reviews diff, approves or rejects
        # self._wait_for_human()     → waits for /approve or /reject comment
        # self._merge_and_close()    → merges PR, closes issue


# ── Entry point ───────────────────────────────────────────────────────────────

def run(payload: dict, github: GitHub, openai_api_key: str) -> None:
    """Kept for main.py compatibility — creates the pipeline and runs it."""
    SDLCPipeline(payload, github, openai_api_key).run()
