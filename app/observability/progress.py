"""
ProgressTracker — reports pipeline progress by posting comments on the GitHub issue.

Isolates all "what to say to the tenant" logic from the orchestrator.
The orchestrator just calls tracker.started() / tracker.feasible() / tracker.done() etc.
"""
import json
import logging

from app.services.github import GitHub

log = logging.getLogger("worker.orchestrator")


class ProgressTracker:
    """Post progress updates as comments on the tenant's issue."""

    def __init__(self, github: GitHub, issue_repo: str, issue_number: int) -> None:
        self._github       = github
        self._issue_repo   = issue_repo
        self._issue_number = issue_number

    def _post(self, message: str) -> None:
        """Post a comment on the issue — swallow errors so pipeline never fails from a bad comment."""
        try:
            self._github.comment_on_issue(self._issue_repo, self._issue_number, message)
        except Exception as exc:
            log.warning(json.dumps({"event": "progress_comment_failed", "error": str(exc)}))

    # ── Callback for tools (search_code, get_dependencies) ────────────────────

    def __call__(self, message: str) -> None:
        """Allow tracker to be used as an on_progress callback in tools."""
        self._post(message)

    # ── High-level events ─────────────────────────────────────────────────────

    def started(self, requirement: str) -> None:
        self._post(f"🤖 SDLC Agent started investigating...\n\nRequirement: {requirement[:300]}")

    def not_feasible(self, summary: str) -> None:
        self._post(f"❌ Not feasible\n\n{summary}")

    def already_implemented(self, summary: str) -> None:
        self._post(f"✅ Already implemented\n\n{summary}")

    def feasible(self, repo: str, files: list[str], summary: str) -> None:
        file_list = ", ".join(files)
        self._post(
            f"✅ Analysis complete — Feasible\n\n"
            f"**Repo:** {repo}\n"
            f"**Files to change:** {file_list}\n"
            f"**Plan:** {summary}\n\n"
            f"⏳ Creating feature branch..."
        )

    def branch_created(self, branch: str, repo: str) -> None:
        self._post(
            f"🌿 Branch created\n\n"
            f"**Branch:** `{branch}`\n"
            f"**Repo:** {repo}\n\n"
            f"⏳ Handing off to Coder agent..."
        )
