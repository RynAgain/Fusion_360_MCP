"""
config/settings.py
Artifex360 -- Persistent settings management -- reads/writes config/config.json

API keys are obfuscated with base64 encoding in the config file (prefix ``enc:``).
This is defence-in-depth obfuscation, **not** cryptographic security -- it
prevents casual inspection of the JSON file but should not be considered
secure storage.  For production use, set the ``ANTHROPIC_API_KEY`` environment
variable or use a ``.env`` file (loaded via python-dotenv).
"""

import base64
import json
import logging
import os
import tempfile
import threading
from typing import Any

logger = logging.getLogger(__name__)

CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

DEFAULTS: dict[str, Any] = {
    "anthropic_api_key": "",
    "model": "claude-sonnet-4-20250514",  # TASK-016: Use valid Anthropic model ID
    "max_tokens": 4096,
    "system_prompt": (
        "You are an expert CAD engineer assistant controlling Autodesk Fusion 360 "
        "via an MCP (Model Context Protocol) server. When the user asks you to create "
        "or modify geometry, use the available tools to execute commands in Fusion 360. "
        "Always confirm what you did after each action. Be concise and precise."
    ),
    "require_confirmation": False,
    "allowed_commands": [],          # empty = all allowed
    "max_requests_per_minute": 10,
    "theme": "dark",
    "window_width": 1200,
    "window_height": 800,
    # -- LLM provider settings --
    "provider": "anthropic",                        # "anthropic" or "ollama"
    "ollama_base_url": "http://localhost:11434",
    "ollama_model": "llama3.1",
    "ollama_num_ctx": None,                         # Context size override (None = use model default)
    "ollama_api_key": None,                         # Bearer token for remote/authenticated Ollama
    # -- Anthropic prompt caching --
    "anthropic_prompt_cache_enabled": True,         # Enable/disable prompt caching
    # -- Anthropic reasoning budget (extended thinking) --
    "anthropic_reasoning_enabled": False,            # Global toggle for extended thinking
    "anthropic_reasoning_budget": 8192,              # Default budget tokens for reasoning
    # -- Fusion operation time budget --
    "fusion_operation_timeout": 120,                 # Seconds per Fusion 360 operation
    "fusion_operation_timeout_action": "abort",      # "abort" or "warn" on timeout
    # -- Git-based design state tracking --
    "git_design_tracking_enabled": False,             # Enable git-based design iteration tracking
    "git_design_branch_prefix": "design",             # Branch prefix for design iterations
    "git_design_state_dir": "data/design_states",     # Directory for design state JSON files
    # -- Prompt-based error policy --
    "prompt_error_policy_enabled": True,              # Include error handling policy in system prompt
    # -- Anthropic 1M context beta --
    "anthropic_1m_context_enabled": False,            # Opt-in to 1M context beta for supported models
    # -- Web search --
    "web_search_enabled": True,                        # Enable/disable web search capability
    "web_search_backend": "duckduckgo",                # "duckduckgo" or "searxng"
    "web_search_searxng_url": None,                    # Base URL for SearXNG instance
    "web_search_max_results": 5,                       # Default number of search results
    "web_search_timeout": 10,                          # Seconds per HTTP request
    # -- Auto-approval (TASK-161) --
    "auto_approval_enabled": False,
    "auto_approval_max_requests": 25,
    "auto_approval_max_cost": 1.0,
    # -- Experiment / feature flags (TASK-170) --
    "experiments": {},                                  # Overrides for ExperimentId flags
    # -- Condensation thresholds (TASK-180) --
    "condense_threshold": 0.65,                        # Fraction of context window to trigger condensation
    "condense_preserve_recent_turns": 4,               # Number of recent turns to keep uncondensed
    "condense_strategy": "hybrid",                     # "llm", "rule_based", or "hybrid"
    # -- Summarization provider (TASK-176) --
    "summarization_provider": None,                    # None = use main provider, or "anthropic"/"ollama"
    "summarization_model": None,                       # None = use provider default
}


class Settings:
    """Singleton-style settings manager backed by a JSON file.

    TASK-029: Lazy initialization -- ``__init__`` no longer calls ``load()``.
    The config file is read on first property or ``get()`` access.  This
    prevents a malformed config from blocking all imports and lets tests
    control the load lifecycle.
    """

    # TASK-054: Only these keys may be set via the web API.
    _SETTABLE_KEYS = frozenset({
        'anthropic_api_key', 'model', 'max_tokens', 'provider',
        'ollama_base_url', 'ollama_model', 'ollama_num_ctx', 'theme',
        'screenshot_enabled', 'auto_screenshot',
        'git_design_tracking_enabled', 'fusion_auto_connect',
        'prompt_error_policy_enabled', 'system_prompt_additions',
        'auto_approval_enabled', 'auto_approval_max_requests',   # TASK-161
        'auto_approval_max_cost',                                 # TASK-161
        'experiments',                                           # TASK-170
        'condense_threshold', 'condense_preserve_recent_turns',  # TASK-180
        'condense_strategy',                                     # TASK-180
        'summarization_provider', 'summarization_model',         # TASK-176
    })

    def __init__(self):
        self._data: dict[str, Any] = dict(DEFAULTS)
        self._loaded: bool = False  # TASK-029: lazy-load flag

    def _ensure_loaded(self) -> None:
        """Load from disk on first access (lazy initialization)."""
        if not self._loaded:
            self.load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load settings from disk, falling back to defaults for missing keys."""
        self._loaded = True  # TASK-029: Mark as loaded even on failure (use defaults)
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                # Merge: saved values override defaults, but keep new default keys
                for key, default_val in DEFAULTS.items():
                    self._data[key] = saved.get(key, default_val)
            except (json.JSONDecodeError, OSError) as exc:
                print(f"[Settings] Could not load config: {exc}. Using defaults.")

    # TASK-104: Serialize concurrent saves to prevent race conditions.
    _save_lock = threading.Lock()

    def save(self) -> None:
        """Persist current settings to disk.

        TASK-104: Uses a threading lock + write-to-temp-then-rename for
        atomicity.  This prevents half-written files if two threads call
        save() concurrently or if the process crashes mid-write.
        """
        with self._save_lock:
            try:
                os.makedirs(CONFIG_DIR, exist_ok=True)
                # Atomic write: write to a temp file, then os.replace()
                tmp_fd, tmp_path = tempfile.mkstemp(
                    dir=CONFIG_DIR, suffix=".tmp",
                )
                try:
                    with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                        json.dump(self._data, f, indent=2)
                    os.replace(tmp_path, CONFIG_FILE)  # atomic on most OS
                except Exception:
                    # Clean up the temp file on failure
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    raise
            except OSError as exc:
                print(f"[Settings] Could not save config: {exc}")

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get(self, key: str, fallback: Any = None) -> Any:
        self._ensure_loaded()
        return self._data.get(key, fallback)

    def set(self, key: str, value: Any, *, _internal: bool = False) -> None:
        """Set a single setting value.

        TASK-054: When called from external context (``_internal=False``),
        the key is validated against ``_SETTABLE_KEYS``.  Internal callers
        can bypass the check with ``_internal=True``.
        """
        if not _internal and key not in self._SETTABLE_KEYS:
            raise ValueError(
                f"Setting key '{key}' is not externally settable. "
                f"Allowed keys: {sorted(self._SETTABLE_KEYS)}"
            )
        self._data[key] = value

    def update(self, mapping: dict[str, Any]) -> None:
        """Bulk-update settings and save.

        TASK-054: Keys not in ``_SETTABLE_KEYS`` are silently filtered out
        (with a warning log) to prevent injection of arbitrary settings
        via the web API.

        If the mapping contains ``anthropic_api_key``, the value is
        automatically obfuscated before being written to disk.
        """
        # TASK-054: Filter to allowed keys only
        rejected = {k for k in mapping if k not in self._SETTABLE_KEYS}
        if rejected:
            logger.warning(
                "TASK-054: Rejected non-settable keys in update(): %s", sorted(rejected)
            )
            mapping = {k: v for k, v in mapping.items() if k in self._SETTABLE_KEYS}

        # Encode the API key before storing
        if "anthropic_api_key" in mapping:
            raw_key = mapping["anthropic_api_key"]
            if raw_key:
                # Security: avoid double-encoding -- if the value already has
                # the ``enc:`` prefix it was previously encoded; store as-is.
                if not raw_key.startswith("enc:"):
                    mapping["anthropic_api_key"] = "enc:" + _encode_key(raw_key)
                # else: already encoded, keep as-is
            # else: keep empty string as-is

        self._data.update(mapping)
        self.save()

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        """Fallback attribute access -- ensure settings are loaded.

        TASK-029: This catches attribute access that isn't a defined
        property (e.g. settings.some_custom_key) and ensures lazy load.
        """
        # Avoid recursion for internal attributes
        if name.startswith("_"):
            raise AttributeError(name)
        self._ensure_loaded()
        if name in self._data:
            return self._data[name]
        raise AttributeError(f"Settings has no attribute '{name}'")

    @property
    def api_key(self) -> str:
        """Return the effective API key.

        Priority order:
        1. ``ANTHROPIC_API_KEY`` environment variable
        2. Obfuscated value in config (``enc:...``)
        3. Plain-text value in config (legacy)
        """
        self._ensure_loaded()
        env_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if env_key:
            return env_key

        stored = self._data.get("anthropic_api_key", "")
        if stored.startswith("enc:"):
            return _decode_key(stored[4:])
        return stored

    @property
    def model(self) -> str:
        self._ensure_loaded()
        return self._data.get("model", DEFAULTS["model"])

    @property
    def max_tokens(self) -> int:
        self._ensure_loaded()
        return int(self._data.get("max_tokens", DEFAULTS["max_tokens"]))

    @property
    def system_prompt(self) -> str:
        self._ensure_loaded()
        return self._data.get("system_prompt", DEFAULTS["system_prompt"])

    @property
    def require_confirmation(self) -> bool:
        self._ensure_loaded()
        return bool(self._data.get("require_confirmation", False))

    @property
    def theme(self) -> str:
        self._ensure_loaded()
        return self._data.get("theme", "dark")

    @property
    def provider(self) -> str:
        self._ensure_loaded()
        return self._data.get("provider", DEFAULTS["provider"])

    @property
    def ollama_base_url(self) -> str:
        self._ensure_loaded()
        return self._data.get("ollama_base_url", DEFAULTS["ollama_base_url"])

    @property
    def ollama_model(self) -> str:
        self._ensure_loaded()
        return self._data.get("ollama_model", DEFAULTS["ollama_model"])

    @property
    def ollama_num_ctx(self) -> int | None:
        self._ensure_loaded()
        return self._data.get("ollama_num_ctx", DEFAULTS["ollama_num_ctx"])

    @property
    def ollama_api_key(self) -> str | None:
        """Return the Ollama API key for remote/authenticated instances.

        Priority: OLLAMA_API_KEY env var > config file value.
        """
        self._ensure_loaded()
        env_key = os.environ.get("OLLAMA_API_KEY", "")
        if env_key:
            return env_key
        return self._data.get("ollama_api_key", DEFAULTS["ollama_api_key"])

    @property
    def anthropic_prompt_cache_enabled(self) -> bool:
        self._ensure_loaded()
        return bool(self._data.get(
            "anthropic_prompt_cache_enabled",
            DEFAULTS["anthropic_prompt_cache_enabled"],
        ))

    @property
    def anthropic_reasoning_enabled(self) -> bool:
        self._ensure_loaded()
        return bool(self._data.get(
            "anthropic_reasoning_enabled",
            DEFAULTS["anthropic_reasoning_enabled"],
        ))

    @property
    def anthropic_reasoning_budget(self) -> int:
        self._ensure_loaded()
        return int(self._data.get(
            "anthropic_reasoning_budget",
            DEFAULTS["anthropic_reasoning_budget"],
        ))

    def to_safe_dict(self) -> dict:
        """Return a curated dict safe for UI exposure.

        TASK-037: Excludes or masks sensitive fields (e.g. the raw API key).
        """
        self._ensure_loaded()
        safe = {}
        # Whitelist of keys safe to expose
        _SAFE_KEYS = {
            "model", "max_tokens", "system_prompt",
            "require_confirmation", "allowed_commands", "max_requests_per_minute",
            "theme", "window_width", "window_height", "provider",
            "ollama_base_url", "ollama_model", "ollama_num_ctx",
            "auto_approval_enabled", "auto_approval_max_requests",    # TASK-161
            "auto_approval_max_cost",                                 # TASK-161
            "experiments",                                            # TASK-170
            "condense_threshold", "condense_preserve_recent_turns",   # TASK-180
            "condense_strategy",                                      # TASK-180
            "summarization_provider", "summarization_model",          # TASK-176
        }
        for key in _SAFE_KEYS:
            if key in self._data:
                safe[key] = self._data[key]

        # Mask API key
        real_key = self.api_key
        if real_key:
            safe["anthropic_api_key"] = (
                real_key[:8] + "..." + real_key[-4:]
                if len(real_key) > 12
                else "***"
            )
        else:
            safe["anthropic_api_key"] = ""

        return safe

    def __repr__(self) -> str:
        safe = dict(self._data)
        if safe.get("anthropic_api_key"):
            safe["anthropic_api_key"] = "***"
        return f"Settings({safe})"


# ----------------------------------------------------------------------
# Key obfuscation helpers
# ----------------------------------------------------------------------

def _encode_key(key: str) -> str:
    """Base64-encode a key string.  NOT cryptographic security."""
    return base64.b64encode(key.encode("utf-8")).decode("utf-8")


def _decode_key(encoded: str) -> str:
    """Decode a base64-obfuscated key string."""
    try:
        return base64.b64decode(encoded.encode("utf-8")).decode("utf-8")
    except Exception:
        return encoded


# Module-level singleton
settings = Settings()
