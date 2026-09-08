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
import json
import logging

from app.agents.analyst import run_analyst
from app.agents.coder import run_coder
from app.agents.reviewer import run_reviewer
from app.observability.progress import ProgressTracker
from app.services.context import ContextError, load_context
from app.services.git import GitRepo
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
        self.branch      = None   # Feature branch name e.g. feat/issue-17-rename-apply-discount-agent
        self.git_repo    = None   # GitRepo — local clone of the code repo
        self.coder_result    = None  # Coder output {status, files_modified, commits}
        self.pr_number       = None  # PR number opened by _create_pr
        self.review_result   = None  # Reviewer output {status, reason}

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

    def _create_branch(self) -> None:
        """
        Build branch name from analyst output and create it on GitHub from main.
        Branch name follows convention: {type}/issue-{number}-{title}-agent
        The -agent suffix is a guardrail — pipeline only merges branches ending in -agent.
        """
        repo   = self.analysis["repo"]
        self.branch = (
            f"{self.analysis['branch_type']}"
            f"/issue-{self.ctx.issue_number}"
            f"-{self.analysis['branch_title']}"
            f"-agent"
        )
        self.github.create_branch(repo, self.branch)
        log.info(json.dumps({"event": "branch_created", "branch": self.branch, "repo": repo}))

        # Clone the repo locally so Coder can read/write files and run tests
        self.git_repo = GitRepo.clone(
            github_org   = self.ctx.github_org,
            repo_name    = repo,
            branch_name  = self.branch,
            token        = self.github._token,
            issue_number = self.ctx.issue_number,
        )
        self.tracker.branch_created(self.branch, repo)

    def _run_coder(self, feedback: str = None) -> None:
        """
        Coder reads files from local clone, implements the change,
        runs tests, commits and pushes to the feature branch.
        feedback — human rejection reason on rework (None on first run).
        """
        self.coder_result = run_coder(
            requirement     = self.ctx.requirement,
            files_to_change = self.analysis["files_to_change"],
            git_repo        = self.git_repo,
            openai_api_key  = self.openai_api_key,
            feedback        = feedback,
        )
        self.tracker.coder_done(self.coder_result)

    def _create_pr(self) -> None:
        """
        Open a PR from the feature branch to main.
        PR body includes the requirement, analyst summary and commits from coder.
        Posts the PR URL as a comment on the issue so tenant can review.
        """
        repo   = self.analysis["repo"]
        commits_text = "\n".join(
            f"- {c.get('message', '')}" for c in self.coder_result.get("commits", [])
        )
        body = (
            f"**Requirement:**\n{self.ctx.requirement}\n\n"
            f"**Analysis:**\n{self.analysis.get('summary', '')}\n\n"
            f"**Commits:**\n{commits_text}\n\n"
            f"Closes #{self.ctx.issue_number}"
        )

        pr = self.github.create_pr(
            repo   = repo,
            branch = self.branch,
            title  = self.ctx.issue_title,
            body   = body,
        )

        self.pr_number = pr["pr_number"]
        log.info(json.dumps({"event": "pr_created", "pr_number": self.pr_number, "url": pr["url"]}))
        self.tracker.pr_created(pr["url"], self.pr_number)

    def _run_reviewer(self) -> None:
        """
        Reviewer independently reads requirement + PR diff.
        No Coder reasoning shared — genuine second opinion.
        If rejected: Coder reworks on same branch, PR stays open.
        """
        repo     = self.analysis["repo"]
        pr_diff  = self.github.get_pr_diff(repo, self.pr_number)
        self.review_result = run_reviewer(self.ctx.requirement, pr_diff, self.openai_api_key)
        self.tracker.reviewer_done(self.review_result)

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

        # Step 5 — Create feature branch on GitHub
        # Branch name: {type}/issue-{number}-{title}-agent
        # Posts branch name as comment on the issue
        self._create_branch()

        # Step 6 — Coder implements the change
        # LLM reads files from local clone, writes changes, runs tests, commits + pushes
        self._run_coder()

        if self.coder_result.get("status") == "failed":
            return

        # Step 7 — Create PR
        # Opens PR from feature branch → main
        # Posts PR URL as comment on the issue
        self._create_pr()

        # Step 8 — Reviewer loop (max 2 rejections)
        # Reviewer reads requirement + PR diff independently
        # If rejected → Coder reworks on same branch (PR stays open) → Reviewer reviews again
        MAX_REVIEWER_REJECTIONS = 2
        reviewer_rejections     = 0

        while True:
            self._run_reviewer()

            if self.review_result["status"] == "approved":
                break

            reviewer_rejections += 1
            if reviewer_rejections >= MAX_REVIEWER_REJECTIONS:
                log.error(json.dumps({"event": "reviewer_max_rejections"}))
                return

            # Coder reworks with reviewer feedback — pushes to same branch, PR auto-updates
            self._run_coder(feedback=self.review_result["reason"])

        # Step 9 — Notify human (PR approved by agent, waiting for human)
        self.tracker.ready_for_human()

        # TODO: self._wait_for_human()
        # TODO: self._merge_and_close()

        # Step 6 — Reviewer reviews the PR (TODO)
        # self._run_reviewer()       → LLM reviews diff, approves or rejects
        # self._wait_for_human()     → waits for /approve or /reject comment
        # self._merge_and_close()    → merges PR, closes issue


# ── Entry point ───────────────────────────────────────────────────────────────

def run(payload: dict, github: GitHub, openai_api_key: str) -> None:
    """Kept for main.py compatibility — creates the pipeline and runs it."""
    SDLCPipeline(payload, github, openai_api_key).run()
