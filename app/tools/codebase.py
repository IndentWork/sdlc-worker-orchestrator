"""
Codebase tools — read-only tools for the Analyst agent.

Three tools:
  search_code      — find relevant code via semantic search (Azure AI Search)
  get_dependencies — find what a symbol calls / is called by (Cosmos DB)
  read_file        — read a file's source code (GitHub API)

Why a factory function?
  Tools need resource_code, repos and github injected so the LLM never sees them.
  The LLM only decides: what to search, which symbol, which file.

  resource_code + repos — narrows search to this tenant's project repos only
  github               — used by read_file to fetch from GitHub API
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


def make_read_tools(resource_code: str, repos: list[str], github) -> list:
    """
    Create read-only tools bound to a specific tenant and project repos.

    resource_code — tenant identifier for filtering
    repos         — only search within these repos (e.g. ['cart-service', 'order-service'])
    github        — GitHub client for read_file
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
            results = client.search(
                search_text=query,
                filter=filter_expr,
                select=["repo", "file", "type", "name", "content"],
                top=5,
            )
            return [
                {
                    "repo":    r.get("repo"),
                    "file":    r.get("file"),
                    "type":    r.get("type"),
                    "name":    r.get("name"),
                    "content": r.get("content", "")[:500],
                }
                for r in results
            ]

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

        return [
            {
                "repo":   n.get("repo"),
                "file":   n.get("file"),
                "type":   n.get("type"),
                "symbol": n.get("name"),
                "calls":  n.get("calls", []),
            }
            for n in nodes
        ]

    @tool
    def read_file(repo: str, file_path: str) -> dict:
        """
        Read the source code of a file from the GitHub repository.
        Use this to inspect the current implementation before deciding what to change.
        repo      — repository name e.g. 'cart-service'
        file_path — relative path e.g. 'cart/main.py'
        """
        content = asyncio.get_event_loop().run_until_complete(
            github.get_file_content(repo, file_path)
        )
        return {"repo": repo, "file": file_path, "content": content}

    return [search_code, get_dependencies, read_file]
