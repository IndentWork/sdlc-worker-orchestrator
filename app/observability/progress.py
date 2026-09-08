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

    def pr_created(self, pr_url: str, pr_number: int) -> None:
        self._post(
            f"🔀 Pull Request created\n\n"
            f"**PR #{pr_number}:** {pr_url}\n\n"
            f"Please review and comment `/approve` to merge or `/reject <reason>` to request changes."
        )

    def reviewer_done(self, result: dict) -> None:
        status = result.get("status")
        reason = result.get("reason", "")
        if status == "approved":
            self._post(f"✅ Reviewer approved\n\n{reason}")
        else:
            self._post(f"🔄 Reviewer rejected — sending back to Coder\n\n**Reason:** {reason}")

    def ready_for_human(self) -> None:
        self._post(
            "🧑 Agent review complete — ready for your review\n\n"
            "Comment `/approve` to merge or `/reject <reason>` to request changes."
        )

    def coder_done(self, result: dict) -> None:
        status = result.get("status")
        commits = result.get("commits", [])
        files   = ", ".join(result.get("files_modified", []))

        if status == "failed":
            last = commits[-1] if commits else {}
            self._post(
                f"❌ Coder failed\n\n"
                f"**Reason:** {last.get('test_results', 'unknown error')}"
            )
        else:
            commit_lines = "\n".join(
                f"- `{c.get('message', '')}` — {c.get('test_results', '')}"
                for c in commits
            )
            self._post(
                f"✅ Coder complete\n\n"
                f"**Files modified:** {files}\n"
                f"**Commits:**\n{commit_lines}\n\n"
                f"⏳ Creating PR..."
            )
