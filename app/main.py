"""
SDLC Orchestrator Worker — listens on Service Bus for orchestrate messages.

Sync worker — one message processed at a time.

Environment variables required:
  SERVICEBUS_NAMESPACE  — e.g. sb-sdlc-shared-dev.servicebus.windows.net
  AZURE_CLIENT_ID       — Managed Identity client ID
  GITHUB_APP_ID         — GitHub App ID (4826692)
  KEY_VAULT_URL         — e.g. https://kv-sdlc-base-dev.vault.azure.net
  ENV                   — dev or prod
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone

from azure.identity import DefaultAzureCredential
from azure.servicebus import ServiceBusClient

from app.orchestrator import run
from app.services.github import GitHub
from app.services.keyvault import get_github_app_private_key, get_openai_api_key

TOPIC_NAME        = "sdlc-events"
SUBSCRIPTION_NAME = "orchestrator"


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

def _process_message(payload: dict) -> None:
    """Process one orchestrate message — runs the full pipeline (sync)."""
    github_org   = payload.get("github_org")
    issue_number = payload.get("issue_number")
    issue_repo   = payload.get("issue_repo")

    log.info(json.dumps({
        "event":        "orchestration_started",
        "github_org":   github_org,
        "issue_repo":   issue_repo,
        "issue_number": issue_number,
    }))

    # read secrets from Key Vault
    app_id         = os.environ["GITHUB_APP_ID"]
    private_key    = get_github_app_private_key()
    openai_api_key = get_openai_api_key()

    # create GitHub client for this pipeline run
    github = GitHub.create(github_org, app_id, private_key)

    # run orchestrator
    run(payload, github, openai_api_key)


# ── Service Bus listener ──────────────────────────────────────────────────────

def listen() -> None:
    """Long-running loop — receives messages from orchestrator subscription (sync)."""
    namespace  = os.environ["SERVICEBUS_NAMESPACE"]
    credential = DefaultAzureCredential()

    log.info(json.dumps({
        "event":        "worker_started",
        "namespace":    namespace,
        "topic":        TOPIC_NAME,
        "subscription": SUBSCRIPTION_NAME,
    }))

    with ServiceBusClient(namespace, credential) as client:
        with client.get_subscription_receiver(TOPIC_NAME, SUBSCRIPTION_NAME) as receiver:
            for message in receiver:
                try:
                    payload = json.loads(str(message))
                    _process_message(payload)
                    receiver.complete_message(message)
                except Exception as exc:
                    log.error(json.dumps({
                        "event": "message_failed",
                        "error": str(exc),
                        "type":  type(exc).__name__,
                    }))
                    receiver.abandon_message(message)


if __name__ == "__main__":
    listen()
