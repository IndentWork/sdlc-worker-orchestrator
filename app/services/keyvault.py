"""
Key Vault service — reads secrets from Azure Key Vault.

Sync client — called at message processing time to fetch secrets.
"""
import os

from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

GITHUB_APP_PRIVATE_KEY_SECRET = "github-app-private-key"
OPENAI_API_KEY_SECRET         = "openai-api-key"


def _vault_url() -> str:
    """Derive Key Vault URL from environment."""
    env = os.environ.get("ENV", "dev")
    return os.environ.get("KEY_VAULT_URL", f"https://kv-sdlc-base-{env}.vault.azure.net")


def _get_secret(name: str) -> str:
    """Read a single secret from Key Vault (sync)."""
    credential = DefaultAzureCredential()
    with SecretClient(_vault_url(), credential) as client:
        secret = client.get_secret(name)
        return secret.value


def get_github_app_private_key() -> str:
    """Read the GitHub App private key PEM from Key Vault."""
    return _get_secret(GITHUB_APP_PRIVATE_KEY_SECRET)


def get_openai_api_key() -> str:
    """Read the OpenAI API key from Key Vault."""
    return _get_secret(OPENAI_API_KEY_SECRET)
