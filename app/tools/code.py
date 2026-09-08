"""
Code tools — tools for the Coder agent to read, write, test and commit code.

Five tools:
  read_file      — read current file content from local clone
  write_file     — write complete updated file content to local clone
  run_tests      — run pytest in the local clone
  commit_changes — git commit staged changes
  push_branch    — git push to GitHub

Why a factory function?
  Tools need git_repo injected so the LLM never sees the local path.
  The LLM only decides: which file to read/write, what content, what commit message.
"""
import json
import subprocess

from langchain_core.tools import tool

from app.services.git import GitRepo


def make_coder_tools(git_repo: GitRepo) -> list:
    """
    Create coder tools bound to a local git clone.
    LLM decides which files to read/write — git_repo is injected via closure.
    """

    @tool
    def read_file(file_path: str) -> str:
        """
        Read the current content of a file from the repository.
        Always read before writing — never guess at existing content.
        file_path — relative path e.g. 'cart/service.py'
        """
        return git_repo.read_file(file_path)

    @tool
    def write_file(file_path: str, content: str) -> dict:
        """
        Write complete file content to the repository.
        The content REPLACES the entire file — include everything, not just the changed part.
        file_path — relative path e.g. 'cart/service.py'
        content   — complete new file content
        """
        git_repo.write_file(file_path, content)
        return {"status": "written", "file": file_path}

    @tool
    def run_tests(test_path: str = ".") -> dict:
        """
        Run pytest in the repository and return results.
        test_path — optional path to limit scope e.g. 'tests/test_cart.py'
        """
        cmd = ["uv", "run", "--group", "dev", "pytest", test_path, "-q", "--tb=short"]
        result = subprocess.run(
            cmd,
            cwd=git_repo._path,
            capture_output=True,
            text=True,
        )
        # treat "no tests collected" as passed — not a test failure
        no_tests = "no tests ran" in result.stdout or "collected 0 items" in result.stdout
        passed   = result.returncode == 0 or no_tests

        return {
            "passed":      passed,
            "stdout":      result.stdout[-2000:],
            "stderr":      result.stderr[-500:],
            "return_code": result.returncode,
            "no_tests":    no_tests,
        }

    @tool
    def commit_changes(message: str) -> dict:
        """
        Stage all changes and commit with the given message.
        message — conventional commit message e.g. 'refactor: rename apply_discount'
        """
        git_repo.commit(message)
        return {"status": "committed", "message": message}

    @tool
    def push_branch() -> dict:
        """Push the branch to GitHub. Call only when all commits are done and tests pass."""
        git_repo.push()
        return {"status": "pushed"}

    return [read_file, write_file, run_tests, commit_changes, push_branch]
