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

Your job is to identify WHICH files need to change for a given requirement.

You have two tools:
  search_code(query)       — search codebase, returns functions/classes with docstring + code snippet
  get_dependencies(symbol) — find what a symbol calls and what calls it

Follow this EXACT sequence — maximum 2 loops:

LOOP 1:
  - Call search_code with a broad query
  - Call get_dependencies on the most relevant symbol found
  - Read the docstrings and content returned

LOOP 2 (only if loop 1 results were unclear or insufficient):
  - Call search_code with a more specific query
  - Call get_dependencies on newly found symbols

After the loops, return your JSON immediately. Do NOT search again.

If nothing relevant was found in both loops:
  → status: "not_feasible", summary: "No existing code found — this may be a new feature"

Return ONLY a JSON object — no explanation, no markdown:
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
    Run the analyst agent (sync).

    requirement    — the user's plain-English requirement
    tools          — sync tools: [search_code, get_dependencies]
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

    MAX_TOOL_CALLS  = 4  # 2 loops × (search_code + get_dependencies)
    tool_call_count = 0

    while True:
        # safety net — force conclusion if LLM ignores the system prompt limit
        if tool_call_count >= MAX_TOOL_CALLS:
            messages.append(HumanMessage(
                "You have done enough searches. Return your final JSON answer now."
            ))
            log.info(json.dumps({"event": "analyst_forced_conclusion", "tool_calls": tool_call_count}))

        response = llm_with_tools.invoke(messages)
        messages.append(response)

        # No tool calls — LLM is done, parse the JSON result
        if not response.tool_calls:
            text   = re.sub(r"```(?:json)?\s*", "", response.content).strip()
            result = json.loads(text)
            log.info(json.dumps({
                "event":      "analyst_done",
                "status":     result.get("status"),
                "repo":       result.get("repo"),
                "tool_calls": tool_call_count,
            }))
            return result

        # Execute each tool the LLM requested
        for call in response.tool_calls:
            tool_call_count += 1
            log.info(json.dumps({
                "event": "tool_called",
                "tool":  call["name"],
                "args":  call["args"],
                "count": tool_call_count,
            }))

            try:
                result = tool_map[call["name"]].invoke(call["args"])
                log.info(json.dumps({
                    "event":        "tool_result",
                    "tool":         call["name"],
                    "result_count": len(result) if isinstance(result, list) else 1,
                    "result":       str(result)[:300],
                }))
            except Exception as exc:
                result = {"error": str(exc)}
                log.error(json.dumps({"event": "tool_error", "tool": call["name"], "error": str(exc)}))

            messages.append(ToolMessage(
                content=json.dumps(result, default=str),
                tool_call_id=call["id"],
            ))
