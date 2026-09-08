"""
Coder Agent — implements a code change, runs tests, commits and pushes.

Workflow (enforced by system prompt):
  1. Read every file that needs to change
  2. Implement the minimal change
  3. Run tests
  4. If pass → commit and push
  5. If fail → fix and retry (max 3 attempts)
  6. Return JSON result

Tools live in app/tools/code.py — same pattern as analyst using app/tools/codebase.py.
"""
import json
import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

from app.services.git import GitRepo
from app.tools.code import make_coder_tools

log = logging.getLogger("worker.orchestrator")

MAX_ITERATIONS = 30  # hard safety net — prevents runaway loops
MAX_TEST_FAILS = 3   # stop after 3 consecutive test failures

SYSTEM_PROMPT = """
You are a very senior software engineer implementing a code change.

Your workflow:

STEP 1 — Read first
  Read every file that needs to change. Understand the existing code completely
  before touching anything. Never guess at what is already there.

STEP 2 — Implement the minimal change
  Make ONLY the change needed to satisfy the requirement.
  Keep ALL existing code — imports, helpers, comments, docstrings.
  When calling write_file, provide the COMPLETE file content — not a partial patch.
  Never omit existing methods, classes, or imports.
  Match existing code style exactly.

  IMPORTANT — update ALL references:
  If you rename a function, class, or variable, search for ALL references to it.
  This includes test files (tests/), other source files, and imports.
  Tests that call the old name will fail — update them too.
  Run tests to confirm before committing.

STEP 3 — Run tests immediately after writing
  Always run tests after every write. Do not skip this step.

STEP 4 — If tests pass → commit
  Commit message format: type: short description
  Valid types: feat, fix, hotfix, docs, test, chore, refactor
  Example: "refactor: rename apply_discount to set_discount"

STEP 5 — If tests fail → fix and retry (max 3 times)
  Read the failure output carefully. Fix the specific failing case.
  Write the complete fixed file. Run tests again.
  If still failing after 3 attempts — stop and report failed.

STEP 6 — Push when everything is committed and tests pass

Return ONLY a JSON object when done — no explanation, no markdown:
{
  "status": "done" or "failed",
  "files_modified": ["relative/path/to/file.py"],
  "commits": [
    {
      "message":      "conventional commit message",
      "changes":      "plain English description of what changed",
      "test_results": "e.g. '8 passed in 0.30s' or '1 failed'"
    }
  ]
}
"""


def run_coder(
    requirement:     str,
    files_to_change: list[str],
    git_repo:        GitRepo,
    openai_api_key:  str,
    feedback:        str = None,
) -> dict:
    """
    Run the coder agent to implement a code change.

    requirement     — original user requirement
    files_to_change — from analyst: which files need to change
    git_repo        — local clone already on the feature branch
    openai_api_key  — from Key Vault
    feedback        — human rejection reason on rework (None on first run)

    Returns: {status, files_modified, commits}
    """
    llm            = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=openai_api_key)
    tools          = make_coder_tools(git_repo)
    llm_with_tools = llm.bind_tools(tools)
    tool_map       = {t.name: t for t in tools}

    task = (
        f"Requirement: {requirement}\n\n"
        f"Files to change: {', '.join(files_to_change)}\n\n"
        f"The feature branch is already checked out. "
        f"Read the files, implement the change. "
        f"If you rename anything, also update test files (check tests/ folder). "
        f"Run tests, commit, and push."
    )
    if feedback:
        task += f"\n\nThis is a rework. Human feedback:\n{feedback}\nAddress this specifically."

    messages = [
        SystemMessage(SYSTEM_PROMPT),
        HumanMessage(task),
    ]

    log.info(json.dumps({"event": "coder_started", "files": files_to_change}))

    iterations      = 0
    test_fail_count = 0

    while True:
        iterations += 1
        if iterations > MAX_ITERATIONS:
            log.error(json.dumps({"event": "coder_max_iterations"}))
            return {"status": "failed", "files_modified": [], "commits": []}

        response = llm_with_tools.invoke(messages)
        messages.append(response)

        # No tool calls — coder is done, parse JSON result
        if not response.tool_calls:
            text   = re.sub(r"```(?:json)?\s*", "", response.content).strip()
            result = json.loads(text)
            log.info(json.dumps({
                "event":  "coder_done",
                "status": result.get("status"),
                "files":  result.get("files_modified"),
            }))
            return result

        # Execute each tool the LLM requested
        for call in response.tool_calls:
            log.info(json.dumps({"event": "tool_called", "tool": call["name"]}))

            try:
                result  = tool_map[call["name"]].invoke(call["args"])
                success = True
                log.info(json.dumps({
                    "event":  "tool_result",
                    "tool":   call["name"],
                    "result": str(result)[:300],
                }))
            except Exception as exc:
                result  = {"error": str(exc)}
                success = False
                log.error(json.dumps({"event": "tool_error", "tool": call["name"], "error": str(exc)}))

            # Hard test failure counter — enforced in code, not just in prompt
            if call["name"] == "run_tests" and isinstance(result, dict):
                if not result.get("passed", True):
                    test_fail_count += 1
                    log.warning(json.dumps({"event": "test_failed", "attempt": test_fail_count}))
                    if test_fail_count >= MAX_TEST_FAILS:
                        log.error(json.dumps({"event": "coder_max_test_fails"}))
                        return {
                            "status":         "failed",
                            "files_modified": [],
                            "commits":        [{
                                "message":      "tests failed after 3 attempts",
                                "changes":      "could not fix failing tests",
                                "test_results": result.get("stdout", "")[:300],
                            }],
                        }
                else:
                    test_fail_count = 0  # reset on pass

            messages.append(ToolMessage(
                content=json.dumps(result, default=str),
                tool_call_id=call["id"],
            ))
