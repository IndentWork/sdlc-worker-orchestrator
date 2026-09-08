"""
Analyst Agent — investigates the codebase for a given requirement.

Read-only agent — only has search and read tools, cannot write anything.

How it works:
  1. LLM receives the requirement + available tools
  2. LLM calls tools to investigate the codebase
  3. When LLM has enough info, it returns a JSON result
  4. We parse and return that JSON
"""
import json
import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

log = logging.getLogger("worker.orchestrator")

SYSTEM_PROMPT = """
You are a senior software engineering analyst.

Your job is to investigate a codebase and understand what needs to change for a given requirement.

Rules:
- Search the codebase before drawing any conclusion
- Read the relevant files to understand the current implementation
- Identify exactly which repo and which files need to change

When done, return ONLY a JSON object — no explanation, no markdown:
{
  "status": "feasible" or "already_implemented" or "not_feasible",
  "repo": "name of the repo that needs to change",
  "files_to_change": ["relative/path/to/file.py"],
  "summary": "what needs to change and why",
  "branch_type": "feat | fix | hotfix | docs | test | chore | refactor",
  "branch_title": "short-hyphenated-title max 30 chars"
}
"""


def run_analyst(requirement: str, tools: list, openai_api_key: str) -> dict:
    """
    Run the analyst agent.

    requirement    — the user's plain-English requirement
    tools          — [search_code, get_dependencies, read_file]
    openai_api_key — from Key Vault

    Returns dict: {status, repo, files_to_change, summary, branch_type, branch_title}
    """
    llm            = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=openai_api_key)
    llm_with_tools = llm.bind_tools(tools)
    tool_map       = {t.name: t for t in tools}

    messages = [
        SystemMessage(SYSTEM_PROMPT),
        HumanMessage(f"Requirement: {requirement}"),
    ]

    log.info(json.dumps({"event": "analyst_started"}))

    while True:
        response = llm_with_tools.invoke(messages)
        messages.append(response)

        # No tool calls — LLM is done, parse the JSON result
        if not response.tool_calls:
            text   = re.sub(r"```(?:json)?\s*", "", response.content).strip()
            result = json.loads(text)
            log.info(json.dumps({
                "event":  "analyst_done",
                "status": result.get("status"),
                "repo":   result.get("repo"),
            }))
            return result

        # Execute each tool the LLM requested
        for call in response.tool_calls:
            log.info(json.dumps({"event": "tool_called", "tool": call["name"]}))

            try:
                result = tool_map[call["name"]].invoke(call["args"])
            except Exception as exc:
                result = {"error": str(exc)}

            messages.append(ToolMessage(
                content=json.dumps(result, default=str),
                tool_call_id=call["id"],
            ))
