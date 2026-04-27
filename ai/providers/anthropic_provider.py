"""Anthropic Claude provider implementation.

Features:
  - Expanded model registry with full metadata (pricing, capabilities).
  - Anthropic prompt caching support (beta header + cache_control markers).
  - Extended thinking / reasoning budget support.
  - Intelligent max_tokens clamping.
"""
import copy
import logging
from typing import Any

from ai.providers.base import BaseProvider, LLMResponse, _retry_on_transient
from config.settings import settings

logger = logging.getLogger(__name__)

ANTHROPIC_AVAILABLE = False
try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Expanded Claude Model Registry
# ---------------------------------------------------------------------------
# Each entry maps a model ID to a metadata dict containing:
#   max_tokens          - Maximum output tokens the model supports
#   context_window      - Maximum context window size (input + output)
#   supports_images     - Whether the model accepts image content blocks
#   supports_prompt_cache - Whether Anthropic prompt caching is available
#   supports_reasoning_budget - Whether extended thinking / reasoning is supported
#   reasoning_required  - (optional) True if reasoning budget *must* be set
#   input_price         - Cost per million input tokens (USD)
#   output_price        - Cost per million output tokens (USD)
#   cache_write_price   - Cost per million tokens for cache writes (USD)
#   cache_read_price    - Cost per million tokens for cache reads (USD)
#   description         - Human-readable model description
# ---------------------------------------------------------------------------

# TODO: TASK-147 -- This registry is manually maintained and will drift from
# Anthropic's actual model list. Consider fetching from the API at startup
# with fallback to this hardcoded list. See: client.models.list()
ANTHROPIC_MODELS: dict[str, dict[str, Any]] = {
    "claude-sonnet-4-6": {
        "max_tokens": 64000,
        "context_window": 200000,
        "supports_images": True,
        "supports_prompt_cache": True,
        "supports_reasoning_budget": True,
        "supports_1m_context": True,
        "input_price": 3.0,
        "output_price": 15.0,
        "cache_write_price": 3.75,
        "cache_read_price": 0.30,
        "description": "Claude Sonnet 4.6 - Latest balanced model",
    },
    "claude-sonnet-4-5": {
        "max_tokens": 64000,
        "context_window": 200000,
        "supports_images": True,
        "supports_prompt_cache": True,
        "supports_reasoning_budget": True,
        "supports_1m_context": True,
        "input_price": 3.0,
        "output_price": 15.0,
        "cache_write_price": 3.75,
        "cache_read_price": 0.30,
        "description": "Claude Sonnet 4.5 - High capability balanced model",
    },
    "claude-sonnet-4-20250514": {
        "max_tokens": 64000,
        "context_window": 200000,
        "supports_images": True,
        "supports_prompt_cache": True,
        "supports_reasoning_budget": True,
        "supports_1m_context": True,
        "input_price": 3.0,
        "output_price": 15.0,
        "cache_write_price": 3.75,
        "cache_read_price": 0.30,
        "description": "Claude Sonnet 4 - Pinned version",
    },
    "claude-opus-4-6": {
        "max_tokens": 128000,
        "context_window": 200000,
        "supports_images": True,
        "supports_prompt_cache": True,
        "supports_reasoning_budget": True,
        "supports_1m_context": True,
        "input_price": 5.0,
        "output_price": 25.0,
        "cache_write_price": 6.25,
        "cache_read_price": 0.50,
        "description": "Claude Opus 4.6 - Most capable model",
    },
    "claude-opus-4-5-20251101": {
        "max_tokens": 32000,
        "context_window": 200000,
        "supports_images": True,
        "supports_prompt_cache": True,
        "supports_reasoning_budget": True,
        "input_price": 5.0,
        "output_price": 25.0,
        "cache_write_price": 6.25,
        "cache_read_price": 0.50,
        "description": "Claude Opus 4.5 - Pinned version",
    },
    "claude-opus-4-1-20250805": {
        "max_tokens": 32000,
        "context_window": 200000,
        "supports_images": True,
        "supports_prompt_cache": True,
        "supports_reasoning_budget": True,
        "input_price": 15.0,
        "output_price": 75.0,
        "cache_write_price": 18.75,
        "cache_read_price": 1.50,
        "description": "Claude Opus 4.1 - Pinned version",
    },
    "claude-opus-4-20250514": {
        "max_tokens": 32000,
        "context_window": 200000,
        "supports_images": True,
        "supports_prompt_cache": True,
        "supports_reasoning_budget": True,
        "input_price": 15.0,
        "output_price": 75.0,
        "cache_write_price": 18.75,
        "cache_read_price": 1.50,
        "description": "Claude Opus 4 - Pinned version",
    },
    "claude-3-7-sonnet-20250219:thinking": {
        "max_tokens": 128000,
        "context_window": 200000,
        "supports_images": True,
        "supports_prompt_cache": True,
        "supports_reasoning_budget": True,
        "reasoning_required": True,
        "input_price": 3.0,
        "output_price": 15.0,
        "cache_write_price": 3.75,
        "cache_read_price": 0.30,
        "description": "Claude 3.7 Sonnet with required extended thinking",
    },
    "claude-3-7-sonnet-20250219": {
        "max_tokens": 8192,
        "context_window": 200000,
        "supports_images": True,
        "supports_prompt_cache": True,
        "input_price": 3.0,
        "output_price": 15.0,
        "cache_write_price": 3.75,
        "cache_read_price": 0.30,
        "description": "Claude 3.7 Sonnet",
    },
    "claude-3-5-sonnet-20241022": {
        "max_tokens": 8192,
        "context_window": 200000,
        "supports_images": True,
        "supports_prompt_cache": True,
        "input_price": 3.0,
        "output_price": 15.0,
        "cache_write_price": 3.75,
        "cache_read_price": 0.30,
        "description": "Claude 3.5 Sonnet",
    },
    "claude-3-5-haiku-20241022": {
        "max_tokens": 8192,
        "context_window": 200000,
        "supports_images": False,
        "supports_prompt_cache": True,
        "input_price": 1.0,
        "output_price": 5.0,
        "cache_write_price": 1.25,
        "cache_read_price": 0.10,
        "description": "Claude 3.5 Haiku - Fast and affordable",
    },
    "claude-haiku-4-5-20251001": {
        "max_tokens": 64000,
        "context_window": 200000,
        "supports_images": True,
        "supports_prompt_cache": True,
        "supports_reasoning_budget": True,
        "input_price": 1.0,
        "output_price": 5.0,
        "cache_write_price": 1.25,
        "cache_read_price": 0.10,
        "description": "Claude Haiku 4.5 - Fast with extended thinking",
    },
    "claude-3-opus-20240229": {
        "max_tokens": 4096,
        "context_window": 200000,
        "supports_images": True,
        "supports_prompt_cache": True,
        "input_price": 15.0,
        "output_price": 75.0,
        "cache_write_price": 18.75,
        "cache_read_price": 1.50,
        "description": "Claude 3 Opus - Legacy flagship",
    },
    "claude-3-haiku-20240307": {
        "max_tokens": 4096,
        "context_window": 200000,
        "supports_images": True,
        "supports_prompt_cache": True,
        "input_price": 0.25,
        "output_price": 1.25,
        "cache_write_price": 0.30,
        "cache_read_price": 0.03,
        "description": "Claude 3 Haiku - Legacy fast model",
    },
}

# Default model when none is specified or the configured model is unknown.
DEFAULT_MODEL = "claude-sonnet-4-20250514"

# Beta header required for Anthropic prompt caching.
_PROMPT_CACHING_BETA = "prompt-caching-2024-07-31"

# Beta header required for 128k output on :thinking models.
_OUTPUT_128K_BETA = "output-128k-2025-02-19"

# Beta header required for 1M extended context.
_CONTEXT_1M_BETA = "context-1m-2025-08-07"


def get_model_info(model_id: str) -> dict[str, Any] | None:
    """Return metadata for *model_id*, or ``None`` if unknown."""
    return ANTHROPIC_MODELS.get(model_id)


def get_effective_context_window(model_id: str) -> int:
    """Return the effective context window for *model_id*.

    When ``anthropic_1m_context_enabled`` is True in settings **and** the
    model has ``supports_1m_context: True``, returns 1,000,000.
    Otherwise returns the model's base ``context_window`` (default 200,000).

    Note on pricing: when the 1M context beta is active, input tokens
    beyond 200K are billed at 1.5x the base input price.
    """
    info = ANTHROPIC_MODELS.get(model_id)
    base_window = (info or {}).get("context_window", 200_000)

    if not settings.get("anthropic_1m_context_enabled", False):
        return base_window
    if info is None or not info.get("supports_1m_context", False):
        return base_window

    return 1_000_000


class AnthropicProvider(BaseProvider):
    """LLM provider backed by the Anthropic Messages API.

    Supports:
      - Full model registry with metadata lookup.
      - Anthropic prompt caching (beta) -- caches system prompts and the
        last two user messages to reduce latency and cost on multi-turn
        conversations.
    """

    def __init__(self):
        self._client = None
        self._api_key: str = ""
        # Prompt caching is enabled by default; callers can override via
        # configure(prompt_cache_enabled=False).
        self._prompt_cache_enabled: bool = True
        # Reasoning budget configuration -- can be overridden via configure().
        self._reasoning_enabled: bool = False
        self._reasoning_budget: int = 8192

    # -- BaseProvider properties -------------------------------------------

    @property
    def name(self) -> str:
        return "Anthropic"

    @property
    def provider_type(self) -> str:
        return "anthropic"

    # -- Configuration -----------------------------------------------------

    def configure(self, api_key: str = "", **kwargs):
        self._api_key = api_key
        self._prompt_cache_enabled = kwargs.get("prompt_cache_enabled", True)
        self._reasoning_enabled = kwargs.get(
            "reasoning_enabled", settings.anthropic_reasoning_enabled,
        )
        self._reasoning_budget = kwargs.get(
            "reasoning_budget", settings.anthropic_reasoning_budget,
        )
        if ANTHROPIC_AVAILABLE and api_key:
            self._client = anthropic.Anthropic(api_key=api_key)
        else:
            self._client = None

    def is_available(self) -> bool:
        return ANTHROPIC_AVAILABLE and self._client is not None

    # -- Message creation --------------------------------------------------

    def create_message(self, messages, system, tools, max_tokens, model) -> LLMResponse:
        if not self.is_available():
            raise RuntimeError("Anthropic provider not configured")

        # Resolve :thinking suffix before looking up model info.
        api_model, thinking_suffix = self._resolve_model(model)
        model_info = get_model_info(model) or get_model_info(api_model)
        use_cache = self._should_use_cache(model_info)
        use_reasoning = self._should_use_reasoning(model_info, thinking_suffix)

        # Build the keyword arguments for the API call.
        kwargs = self._build_api_kwargs(
            messages=messages,
            system=system,
            tools=tools,
            max_tokens=max_tokens,
            model=api_model,
            model_info=model_info,
            use_cache=use_cache,
            use_reasoning=use_reasoning,
            thinking_suffix=thinking_suffix,
        )

        response = _retry_on_transient(
            lambda: self._client.messages.create(**kwargs)
        )
        return self._convert_response(response, use_cache=use_cache)

    def stream_message(self, messages, system, tools, max_tokens, model,
                       text_callback=None, reasoning_callback=None) -> LLMResponse:
        if not self.is_available():
            raise RuntimeError("Anthropic provider not configured")

        api_model, thinking_suffix = self._resolve_model(model)
        model_info = get_model_info(model) or get_model_info(api_model)
        use_cache = self._should_use_cache(model_info)
        use_reasoning = self._should_use_reasoning(model_info, thinking_suffix)

        kwargs = self._build_api_kwargs(
            messages=messages,
            system=system,
            tools=tools,
            max_tokens=max_tokens,
            model=api_model,
            model_info=model_info,
            use_cache=use_cache,
            use_reasoning=use_reasoning,
            thinking_suffix=thinking_suffix,
        )

        try:
            def _do_stream():
                with self._client.messages.stream(**kwargs) as stream:
                    if text_callback:
                        for text in stream.text_stream:
                            text_callback(text)
                    return stream.get_final_message()

            response = _retry_on_transient(_do_stream)
            return self._convert_response(response, use_cache=use_cache)

        except (AttributeError, TypeError):
            # TASK-044: Older SDK without messages.stream -- fall back to sync call.
            # AttributeError/TypeError can occur if .stream is missing entirely.
            logger.info("Streaming unavailable; falling back to messages.create()")
            result = self.create_message(messages, system, tools, max_tokens, model)
            # Emit text blocks that were not streamed
            if text_callback:
                for block in result.content:
                    if block.get("type") == "text":
                        text_callback(block["text"])
            return result

    # -- Model listing -----------------------------------------------------

    def list_models(self) -> list[dict]:
        """Return a list of available models with ``id``, ``name``, and metadata.

        The returned dicts always include ``id`` and ``name`` (for backward
        compatibility with callers that expect those keys) plus all extra
        metadata fields from the registry.
        """
        result = []
        for model_id, meta in ANTHROPIC_MODELS.items():
            entry: dict[str, Any] = {"id": model_id, "name": meta.get("description", model_id)}
            entry.update(meta)
            result.append(entry)
        return result

    # -- Internal helpers --------------------------------------------------

    @staticmethod
    def _resolve_model(model: str) -> tuple[str, bool]:
        """Resolve a model ID, handling the ``:thinking`` suffix.

        Returns ``(api_model_id, has_thinking_suffix)``.  When the model ID
        ends with ``:thinking`` the suffix is stripped for the actual API call
        and the boolean flag is set so the caller can enable required reasoning
        and add the appropriate beta header.
        """
        if model.endswith(":thinking"):
            return model.removesuffix(":thinking"), True
        return model, False

    def _should_use_cache(self, model_info: dict[str, Any] | None) -> bool:
        """Determine whether prompt caching should be applied for this request.

        Caching is used when:
          1. The provider-level flag ``_prompt_cache_enabled`` is True.
          2. The model's registry entry has ``supports_prompt_cache: True``.
        """
        if not self._prompt_cache_enabled:
            return False
        if model_info is None:
            # Unknown model -- play it safe and skip caching.
            return False
        return bool(model_info.get("supports_prompt_cache", False))

    @staticmethod
    def _should_use_1m_context(model: str, model_info: dict[str, Any] | None = None) -> bool:
        """Determine whether the 1M extended context beta should be used.

        The beta is activated when:
          1. ``anthropic_1m_context_enabled`` is True in settings.
          2. The model has ``supports_1m_context: True`` in the registry.
        """
        if not settings.get("anthropic_1m_context_enabled", False):
            return False
        if model_info is None:
            return False
        return bool(model_info.get("supports_1m_context", False))

    def _should_use_reasoning(
        self,
        model_info: dict[str, Any] | None,
        thinking_suffix: bool = False,
    ) -> bool:
        """Determine whether extended thinking / reasoning should be enabled.

        Reasoning is enabled when **any** of the following is true:
          1. The model has ``reasoning_required: True`` in the registry.
          2. The model ID had a ``:thinking`` suffix (resolved earlier).
          3. The config toggle ``_reasoning_enabled`` is True **and** the model
             has ``supports_reasoning_budget: True``.
        """
        if model_info is not None and model_info.get("reasoning_required"):
            return True
        if thinking_suffix:
            return True
        if not self._reasoning_enabled:
            return False
        if model_info is None:
            return False
        return bool(model_info.get("supports_reasoning_budget", False))

    def _build_api_kwargs(
        self,
        *,
        messages: list,
        system: Any,
        tools: list,
        max_tokens: int,
        model: str,
        model_info: dict[str, Any] | None = None,
        use_cache: bool,
        use_reasoning: bool = False,
        thinking_suffix: bool = False,
    ) -> dict[str, Any]:
        """Build the keyword arguments dict passed to ``messages.create`` / ``messages.stream``.

        When *use_cache* is True the following adjustments are made:
          - ``extra_headers`` includes the prompt-caching beta header.
          - System prompt text blocks are annotated with ``cache_control``.
          - The last two user messages have ``cache_control`` added to their
            content blocks so Anthropic can cache them across turns.

        When *use_reasoning* is True:
          - A ``thinking`` parameter is added with the budget.
          - ``temperature`` is forced to 1.0 (Anthropic requirement).
          - Budget is capped at 80% of the model's ``max_tokens``.

        Max-token clamping is always applied based on model registry metadata.
        """
        prepared_system = self._prepare_system(system, use_cache)
        prepared_messages = self._prepare_messages(messages, use_cache)

        # -- Max-token clamping ------------------------------------------------
        # Use effective context window (1M when beta is enabled for this model).
        context_window = get_effective_context_window(model) if model else (model_info or {}).get("context_window", 200_000)
        model_max_output = (model_info or {}).get("max_tokens", 0)
        clamped_max_tokens = self.clamp_max_tokens(
            max_tokens, context_window, is_reasoning=use_reasoning,
            max_output=model_max_output,
        )

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": clamped_max_tokens,
            "system": prepared_system,
            "messages": prepared_messages,
        }

        # Only include tools if non-empty (some SDK versions reject tools=[]).
        if tools:
            kwargs["tools"] = tools

        # -- Beta headers (accumulated) ----------------------------------------
        beta_parts: list[str] = []
        if use_cache:
            beta_parts.append(_PROMPT_CACHING_BETA)
        if thinking_suffix:
            beta_parts.append(_OUTPUT_128K_BETA)

        # 1M context beta header
        use_1m = self._should_use_1m_context(model, model_info)
        if use_1m:
            beta_parts.append(_CONTEXT_1M_BETA)

        if beta_parts:
            kwargs["extra_headers"] = {
                "anthropic-beta": ",".join(beta_parts),
            }

        # -- Reasoning / extended thinking ------------------------------------
        if use_reasoning:
            model_max = (model_info or {}).get("max_tokens", 64_000)
            budget_cap = int(model_max * 0.8)
            budget = min(self._reasoning_budget, budget_cap)
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}
            # Anthropic requires temperature=1.0 when thinking is enabled.
            kwargs["temperature"] = 1.0

        return kwargs

    # -- Prompt caching helpers --------------------------------------------

    @staticmethod
    def _prepare_system(system: Any, use_cache: bool) -> Any:
        """Wrap the system prompt for caching when appropriate.

        The Anthropic API accepts either a plain string or a list of content
        blocks for the ``system`` parameter.  When caching is active we
        convert to the list-of-blocks form and annotate the last block with
        ``cache_control: {"type": "ephemeral"}`` so Anthropic caches the
        full system prompt prefix.
        """
        if not use_cache or not system:
            return system

        # If system is already a list of blocks, deep-copy so we don't
        # mutate the caller's data.
        if isinstance(system, list):
            blocks = copy.deepcopy(system)
        else:
            # Plain string -- convert to a single text block.
            blocks = [{"type": "text", "text": str(system)}]

        # Mark the *last* block for caching (covers the entire prefix).
        if blocks:
            blocks[-1]["cache_control"] = {"type": "ephemeral"}

        return blocks

    @staticmethod
    def _prepare_messages(messages: list, use_cache: bool) -> list:
        """Annotate the last two user messages with ``cache_control``.

        Anthropic prompt caching works by caching *prefixes* of the
        conversation.  By marking the last two user turns we allow the API
        to cache everything up to (and including) each of those messages,
        which dramatically reduces input token costs on multi-turn chats.

        We deep-copy the messages to avoid mutating the caller's data.
        """
        if not use_cache or not messages:
            return messages

        # Deep-copy so we never mutate the original list.
        msgs = copy.deepcopy(messages)

        # Find indices of user messages (from the end).
        user_indices = [i for i, m in enumerate(msgs) if m.get("role") == "user"]

        # We want to annotate the last two user messages.
        targets = user_indices[-2:] if len(user_indices) >= 2 else user_indices

        for idx in targets:
            msg = msgs[idx]
            content = msg.get("content")

            if isinstance(content, str):
                # Convert plain string to content-block form so we can attach
                # cache_control metadata.
                msg["content"] = [
                    {
                        "type": "text",
                        "text": content,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
            elif isinstance(content, list) and content:
                # Annotate the *last* content block in this message.
                content[-1]["cache_control"] = {"type": "ephemeral"}

        return msgs

    # -- Response conversion -----------------------------------------------

    def _convert_response(self, response, *, use_cache: bool = False) -> LLMResponse:
        """Convert an Anthropic ``Message`` to a standardised ``LLMResponse``.

        When *use_cache* is True, cache-related token counts are extracted
        from the response usage object and included in the usage dict.

        Thinking blocks (``type="thinking"``) are extracted and stored under
        a ``reasoning`` key on the response so callers can inspect the
        model's chain-of-thought separately from the text output.
        """
        result = LLMResponse()
        result.model = response.model
        result.stop_reason = response.stop_reason

        if response.usage:
            result.usage = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }

            # -- Prompt caching usage tracking --
            # The Anthropic API returns additional fields when caching is
            # active.  We capture them so callers can monitor cache
            # effectiveness and cost savings.
            if use_cache:
                cache_creation = getattr(response.usage, "cache_creation_input_tokens", None)
                cache_read = getattr(response.usage, "cache_read_input_tokens", None)

                if cache_creation is not None:
                    result.usage["cache_creation_input_tokens"] = cache_creation
                if cache_read is not None:
                    result.usage["cache_read_input_tokens"] = cache_read

                # Log cache stats for observability.
                if cache_creation or cache_read:
                    logger.info(
                        "Prompt cache stats -- created: %s tokens, read: %s tokens",
                        cache_creation or 0,
                        cache_read or 0,
                    )

        result.content = []
        reasoning_parts: list[str] = []

        for block in response.content:
            if block.type == "text":
                result.content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                result.content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
            elif block.type == "thinking":
                # Extended thinking block -- collect for the reasoning key.
                thinking_text = getattr(block, "thinking", "")
                if thinking_text:
                    reasoning_parts.append(thinking_text)

        # Attach collected reasoning to the response object.
        if reasoning_parts:
            result.reasoning = "\n\n".join(reasoning_parts)

        return result
