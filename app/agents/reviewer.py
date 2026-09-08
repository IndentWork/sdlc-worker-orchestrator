"""
Reviewer Agent — independently reviews a PR diff against the requirement.

No tools — one LLM call with requirement + diff.
Independence is a design invariant: Reviewer never sees the Coder's reasoning,
only the requirement and the actual code change. This ensures a genuine second
opinion, not a rubber stamp.

Returns: {status: "approved"|"rejected", reason: "specific explanation"}
"""
import json
import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

log = logging.getLogger("worker.orchestrator")

SYSTEM_PROMPT = """
You are a senior software engineer doing an independent code review.

You will receive:
  1. The original requirement
  2. The PR diff showing what changed

Your job:
  - Check that the change fully satisfies the requirement
  - Check for missing updates (e.g. renamed in one file but not another)
  - Check for broken logic or obvious bugs
  - Check that the change is minimal — no unnecessary modifications

Return ONLY a JSON object — no explanation, no markdown:
{
  "status": "approved" or "rejected",
  "reason": "specific explanation citing files and line numbers if rejecting"
}

Approve if the change correctly and completely satisfies the requirement.
Reject if something is missing, wrong, or incomplete — be specific about what.
"""


def run_reviewer(
    requirement:   str,
    pr_diff:       str,
    openai_api_key: str,
) -> dict:
    """
    Run the reviewer agent on a PR diff.

    requirement    — the original user requirement
    pr_diff        — unified diff fetched from GitHub API
    openai_api_key — from Key Vault

    Returns: {status: "approved"|"rejected", reason: str}
    """
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=openai_api_key)

    messages = [
        SystemMessage(SYSTEM_PROMPT),
        HumanMessage(
            f"Requirement: {requirement}\n\n"
            f"PR Diff:\n```diff\n{pr_diff}\n```"
        ),
    ]

    log.info(json.dumps({"event": "reviewer_started"}))

    response = llm.invoke(messages)

    text   = re.sub(r"```(?:json)?\s*", "", response.content).strip()
    result = json.loads(text)

    log.info(json.dumps({
        "event":  "reviewer_done",
        "status": result.get("status"),
        "reason": result.get("reason", "")[:200],
    }))

    return result
