"""Provider management -- registry and active-provider switching."""
import logging

from ai.providers.base import BaseProvider
from ai.providers.anthropic_provider import AnthropicProvider
from ai.providers.ollama_provider import OllamaProvider

logger = logging.getLogger(__name__)


class ProviderManager:
    """Manages the set of available LLM providers and the currently active one."""

    def __init__(self, initial_provider: str = "anthropic"):
        self._providers: dict[str, BaseProvider] = {}
        self._active_type: str = initial_provider if initial_provider in ("anthropic", "ollama") else "anthropic"

        # Register built-in providers
        self._providers["anthropic"] = AnthropicProvider()
        self._providers["ollama"] = OllamaProvider()

        logger.info("ProviderManager initialized with active_type=%s", self._active_type)

    # -- Active provider ---------------------------------------------------

    @property
    def active(self) -> BaseProvider:
        """Return the currently active provider instance."""
        return self._providers[self._active_type]

    @property
    def active_type(self) -> str:
        """Return the type key of the active provider."""
        return self._active_type

    def switch(self, provider_type: str) -> BaseProvider:
        """Switch the active provider.  Raises ``ValueError`` for unknowns."""
        if provider_type not in self._providers:
            raise ValueError(
                f"Unknown provider: {provider_type}. "
                f"Available: {list(self._providers.keys())}"
            )
        self._active_type = provider_type
        logger.info("Switched to provider: %s", provider_type)
        return self._providers[provider_type]

    # -- Configuration & introspection -------------------------------------

    def configure_provider(self, provider_type: str, **kwargs):
        """Configure a specific provider by type key."""
        if provider_type in self._providers:
            self._providers[provider_type].configure(**kwargs)

    def get_provider(self, provider_type: str) -> BaseProvider | None:
        """Return a provider instance by type key, or ``None``."""
        return self._providers.get(provider_type)

    def list_providers(self) -> list[dict]:
        """Return metadata for every registered provider."""
        result = []
        for ptype, provider in self._providers.items():
            result.append({
                "type": ptype,
                "name": provider.name,
                "is_active": ptype == self._active_type,
                "is_available": provider.is_available(),
            })
        return result

    def list_models(self, provider_type: str | None = None) -> list[dict]:
        """List models for *provider_type* (defaults to the active provider)."""
        ptype = provider_type or self._active_type
        provider = self._providers.get(ptype)
        if provider:
            return provider.list_models()
        return []
