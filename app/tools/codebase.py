"""
Codebase tools — async tools for investigating the codebase.

Analyst tools (search only):
  search_code      — find relevant functions/classes (Azure AI Search)
  get_dependencies — find relationships for a symbol (Cosmos DB)

Why async?
  The orchestrator runs in an async event loop.
  Sync Azure SDK clients called from an async context throw
  "event loop is already running". Using async clients avoids this.

Why a factory function?
  Tools need resource_code, repos and on_progress injected.
  The LLM only decides: what to search, which symbol.
"""
import os

from azure.cosmos.aio import CosmosClient
from azure.identity.aio import DefaultAzureCredential
from azure.search.documents.aio import SearchClient
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
    Create search-only async tools for the Analyst agent.

    resource_code — tenant identifier for filtering
    repos         — only search within these repos (e.g. ['cart-service', 'order-service'])
    on_progress   — optional async callback(message: str) to post updates on the issue
    """
    # Build repo filter string for AI Search
    # e.g. "repo eq 'cart-service' or repo eq 'order-service'"
    repo_filter = " or ".join(f"repo eq '{r}'" for r in repos)

    @tool
    async def search_code(query: str) -> list:
        """
        Search the codebase semantically. Use this to find where
        functionality, classes, or functions are implemented.
        Returns matching code chunks with repo, file, type, name and content.
        """
        filter_expr = f"resource_code eq '{resource_code}' and ({repo_filter})"

        credential = DefaultAzureCredential()
        async with SearchClient(_search_endpoint(), "code-chunks", credential) as client:
            results = client.search(
                search_text=query,
                filter=filter_expr,
                select=["repo", "file", "type", "name", "content"],
                top=5,
            )
            matches = [
                {
                    "repo":    r.get("repo"),
                    "file":    r.get("file"),
                    "type":    r.get("type"),
                    "name":    r.get("name"),
                    "content": r.get("content", ""),
                }
                async for r in results
            ]

        # post progress comment on the issue
        if on_progress and matches:
            names = ", ".join(m["name"] for m in matches if m.get("name"))
            await on_progress(f'🔎 Searched: **"{query}"**\n   Found: {names}')

        return matches

    @tool
    async def get_dependencies(symbol: str) -> list:
        """
        Get what a code symbol calls and what calls it.
        Use this to understand the impact of changing a function or class.
        """
        repo_list = ", ".join(f"'{r}'" for r in repos)

        credential = DefaultAzureCredential()
        async with CosmosClient(_cosmos_endpoint(), credential) as client:
            container = client.get_database_client("sdlc").get_container_client("nodes")
            nodes = [
                node async for node in container.query_items(
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
            ]

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
            await on_progress(f'🔗 Dependencies for **{symbol}**: calls → {calls}')

        return matches

    return [search_code, get_dependencies]
