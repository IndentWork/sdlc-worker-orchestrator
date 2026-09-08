"""
Git service — clones tenant repos locally so the Coder can read and write files.

Why clone instead of GitHub API?
  - Run pytest locally (real test results before committing)
  - Faster multi-file reads (disk vs HTTP)
  - Standard git workflow (commit, push)

Each pipeline run clones to /tmp/{repo}-{issue_number}/ and cleans up after.
Authentication uses the GitHub App installation token (short-lived, 1 hour).
"""
import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("worker.orchestrator")


class GitRepo:
    """
    A locally cloned GitHub repository for one pipeline run.

    Usage:
        repo = GitRepo.clone(org, repo_name, branch, token, issue_number)
        content = repo.read_file("cart/service.py")
        repo.write_file("cart/service.py", new_content)
        repo.commit("refactor: rename apply_discount to set_discount")
        repo.push()
        repo.cleanup()
    """

    def __init__(self, path: Path, branch: str) -> None:
        self._path   = path
        self._branch = branch

    @classmethod
    def clone(
        cls,
        github_org:   str,
        repo_name:    str,
        branch_name:  str,
        token:        str,
        issue_number: int,
    ) -> "GitRepo":
        """
        Clone the repo and checkout the feature branch.
        Clones to /tmp/{repo_name}-issue-{issue_number}/ for isolation.
        """
        clone_path = Path(f"/tmp/{repo_name}-issue-{issue_number}")

        # Clean up any previous clone for this issue
        if clone_path.exists():
            shutil.rmtree(clone_path)

        clone_url = f"https://x-access-token:{token}@github.com/{github_org}/{repo_name}.git"

        _run(["git", "clone", clone_url, str(clone_path)])
        _run(["git", "checkout", branch_name], cwd=clone_path)
        _run(["git", "config", "user.email", "sdlc-agent@indentwork.com"], cwd=clone_path)
        _run(["git", "config", "user.name", "SDLC Agent"], cwd=clone_path)

        log.info(f"Cloned {github_org}/{repo_name} → {clone_path} on branch {branch_name}")
        return cls(clone_path, branch_name)

    def read_file(self, file_path: str) -> str:
        """Read a file from the local clone."""
        return (self._path / file_path).read_text(encoding="utf-8")

    def write_file(self, file_path: str, content: str) -> None:
        """Write a file to the local clone (does not commit)."""
        full_path = self._path / file_path
        full_path.write_text(content, encoding="utf-8")

    def commit(self, message: str) -> None:
        """Stage all changes and commit."""
        _run(["git", "add", "."], cwd=self._path)
        _run(["git", "commit", "-m", message], cwd=self._path)

    def push(self) -> None:
        """Push the branch to GitHub."""
        _run(["git", "push", "origin", self._branch], cwd=self._path)

    def cleanup(self) -> None:
        """Delete the local clone — called after the pipeline run completes."""
        if self._path.exists():
            shutil.rmtree(self._path)
            log.info(f"Cleaned up {self._path}")


def _run(cmd: list[str], cwd: Path = None) -> str:
    """Run a shell command, raise on failure."""
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git command failed: {' '.join(cmd)}\n{result.stderr}")
    return result.stdout
