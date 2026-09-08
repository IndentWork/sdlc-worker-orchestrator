"""
SDLC Orchestrator Worker — listens on Service Bus for orchestrate messages.

For now: receives message, fetches issue from GitHub, logs title + requirement.
Next: will run Analyst → Coder → Reviewer agents.

Environment variables required:
  SERVICEBUS_NAMESPACE  — e.g. sb-sdlc-shared-dev.servicebus.windows.net
  AZURE_CLIENT_ID       — Managed Identity client ID
  GITHUB_APP_ID         — GitHub App ID (4826692)
  KEY_VAULT_URL         — e.g. https://kv-sdlc-base-dev.vault.azure.net
  ENV                   — dev or prod
"""
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone

from azure.identity.aio import DefaultAzureCredential
from azure.servicebus.aio import ServiceBusClient

from app.services.github import GitHub
from app.services.keyvault import get_github_app_private_key

TOPIC_NAME        = "sdlc-events"
SUBSCRIPTION_NAME = "orchestrator"
SQL_FILTER        = "action = 'orchestrate'"


# ── Logging ───────────────────────────────────────────────────────────────────

class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level":     record.levelname,
            "logger":    record.name,
            "message":   record.getMessage(),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry)


def _setup_logging() -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    return logging.getLogger("worker.orchestrator")


log = _setup_logging()


# ── Message handler ───────────────────────────────────────────────────────────

async def _process_message(payload: dict) -> None:
    """
    Process one orchestrate message.
    Fetches issue from GitHub and logs the requirement.
    Next: will pass requirement to Analyst agent.
    """
    github_org   = payload.get("github_org")
    issue_repo   = payload.get("issue_repo")
    issue_number = payload.get("issue_number")

    log.info(json.dumps({
        "event":        "orchestration_started",
        "github_org":   github_org,
        "issue_repo":   issue_repo,
        "issue_number": issue_number,
    }))

    # get GitHub App token
    app_id      = os.environ["GITHUB_APP_ID"]
    private_key = await get_github_app_private_key()
    github      = await GitHub.create(github_org, app_id, private_key)

    # fetch issue — the user's requirement is in the body
    issue = await github.get_issue(issue_repo, issue_number)

    log.info(json.dumps({
        "event":       "issue_fetched",
        "number":      issue["number"],
        "title":       issue["title"],
        "requirement": issue["body"],
        "url":         issue["url"],
    }))


# ── Service Bus listener ──────────────────────────────────────────────────────

async def listen() -> None:
    """Long-running loop — receives messages from orchestrator subscription."""
    namespace  = os.environ["SERVICEBUS_NAMESPACE"]
    credential = DefaultAzureCredential()

    log.info(json.dumps({
        "event":        "worker_started",
        "namespace":    namespace,
        "topic":        TOPIC_NAME,
        "subscription": SUBSCRIPTION_NAME,
    }))

    async with ServiceBusClient(namespace, credential) as client:
        try:
            await client.create_subscription(
                TOPIC_NAME,
                SUBSCRIPTION_NAME,
                sql_filter=SQL_FILTER,
                exists_ok=True,
            )
        except Exception as exc:
            log.warning(json.dumps({"event": "subscription_warning", "error": str(exc)}))

        async with client.get_subscription_receiver(TOPIC_NAME, SUBSCRIPTION_NAME) as receiver:
            async for message in receiver:
                try:
                    payload = json.loads(str(message))
                    await _process_message(payload)
                    await receiver.complete_message(message)
                except Exception as exc:
                    log.error(json.dumps({
                        "event": "message_failed",
                        "error": str(exc),
                        "type":  type(exc).__name__,
                    }))
                    await receiver.abandon_message(message)


if __name__ == "__main__":
    asyncio.run(listen())
