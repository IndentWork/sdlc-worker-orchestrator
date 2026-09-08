"""
Codebase tools — tools for investigating the codebase.

Analyst tools (search only — no file reading):
  search_code      — find relevant functions/classes (Azure AI Search)
  get_dependencies — find relationships for a symbol (Cosmos DB)

Coder tools (added later):
  read_file        — read full file content (GitHub API)
  write_file       — push changes to GitHub (GitHub API)

Why a factory function?
  Tools need resource_code and repos injected so the LLM never sees them.
  The LLM only decides: what to search, which symbol.
"""
import asyncio
import os

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from langchain_core.tools import tool


def _search_endpoint() -> str:
    env   = os.environ.get("ENV", "dev")
    scope = os.environ.get("SEARCH_SCOPE", "shared")
    return f"https://srch-sdlc-{scope}-{env}.search.windows.net"


def _cosmos_endpoint() -> str:
    env   = os.environ.get("ENV", "dev")
    scope = os.environ.get("COSMOS_SCOPE", "shared")
    return f"https://cosmos-sdlc-{scope}-{env}.documents.azure.com:443/"


def make_analyst_tools(resource_code: str, repos: list[str], on_progress=None) -> list:
    """
    Create search-only tools for the Analyst agent.

    Analyst only searches — never reads full files.
    Files are read by the Coder agent which uses make_coder_tools().

    resource_code — tenant identifier for filtering
    repos         — only search within these repos (e.g. ['cart-service', 'order-service'])
    on_progress   — optional async callback(message: str) to post updates on the issue
    """
    # Build repo filter string for AI Search and Cosmos DB
    # e.g. "repo eq 'cart-service' or repo eq 'order-service'"
    repo_filter = " or ".join(f"repo eq '{r}'" for r in repos)

    @tool
    def search_code(query: str) -> list:
        """
        Search the codebase semantically. Use this to find where
        functionality, classes, or functions are implemented.
        Returns matching code chunks with repo, file, type, name and content.
        """
        filter_expr = f"resource_code eq '{resource_code}' and ({repo_filter})"

        with SearchClient(_search_endpoint(), "code-chunks", DefaultAzureCredential()) as client:
            results = list(client.search(
                search_text=query,
                filter=filter_expr,
                select=["repo", "file", "type", "name", "content"],
                top=5,
            ))

        matches = [
            {
                "repo":    r.get("repo"),
                "file":    r.get("file"),
                "type":    r.get("type"),
                "name":    r.get("name"),
                "content": r.get("content", ""),  # full content — has docstring + signature
            }
            for r in results
        ]

        # post progress comment on the issue
        if on_progress and matches:
            names = ", ".join(m["name"] for m in matches if m.get("name"))
            asyncio.get_event_loop().run_until_complete(
                on_progress(f'🔎 Searched: **"{query}"**\n   Found: {names}')
            )

        return matches

    @tool
    def get_dependencies(symbol: str) -> list:
        """
        Get what a code symbol calls and what calls it.
        Use this to understand the impact of changing a function or class.
        """
        repo_list = ", ".join(f"'{r}'" for r in repos)

        with CosmosClient(_cosmos_endpoint(), DefaultAzureCredential()) as client:
            nodes = list(
                client.get_database_client("sdlc")
                      .get_container_client("nodes")
                      .query_items(
                          query=(
                              "SELECT * FROM c "
                              "WHERE c.resource_code=@rc "
                              "AND c.name=@sym "
                              f"AND c.repo IN ({repo_list})"
                          ),
                          parameters=[
                              {"name": "@rc",  "value": resource_code},
                              {"name": "@sym", "value": symbol},
                          ],
                      )
            )

        if not nodes:
            return [{"message": f"Symbol '{symbol}' not found in index"}]

        matches = [
            {
                "repo":   n.get("repo"),
                "file":   n.get("file"),
                "type":   n.get("type"),
                "symbol": n.get("name"),
                "calls":  n.get("calls", []),
            }
            for n in nodes
        ]

        # post progress comment on the issue
        if on_progress and matches:
            calls = ", ".join(matches[0].get("calls", [])) or "none"
            asyncio.get_event_loop().run_until_complete(
                on_progress(f'🔗 Dependencies for **{symbol}**: calls → {calls}')
            )

        return matches

    return [search_code, get_dependencies]
