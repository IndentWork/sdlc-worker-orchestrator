"""
Key Vault service — reads secrets from Azure Key Vault at startup.

The GitHub App private key is a multi-line PEM file stored in Key Vault.
It is read once at startup and reused for all GitHub token requests.
Authentication uses DefaultAzureCredential (Managed Identity in Azure).
"""
import os

from azure.identity.aio import DefaultAzureCredential
from azure.keyvault.secrets.aio import SecretClient

GITHUB_APP_PRIVATE_KEY_SECRET = "github-app-private-key"
OPENAI_API_KEY_SECRET         = "openai-api-key"


def _vault_url() -> str:
    """Derive Key Vault URL from environment."""
    env = os.environ.get("ENV", "dev")
    return os.environ.get("KEY_VAULT_URL", f"https://kv-sdlc-base-{env}.vault.azure.net")


async def _get_secret(name: str) -> str:
    """Read a single secret from Key Vault."""
    credential = DefaultAzureCredential()
    async with SecretClient(_vault_url(), credential) as client:
        secret = await client.get_secret(name)
        return secret.value


async def get_github_app_private_key() -> str:
    """Read the GitHub App private key PEM from Key Vault."""
    return await _get_secret(GITHUB_APP_PRIVATE_KEY_SECRET)


async def get_openai_api_key() -> str:
    """Read the OpenAI API key from Key Vault."""
    return await _get_secret(OPENAI_API_KEY_SECRET)
