"""Ollama local LLM provider with native SDK support and model discovery.

Features:
  - Native ``ollama`` Python SDK with HTTP fallback (graceful degradation).
  - Two-phase model discovery: list models, then fetch detailed metadata.
  - Tool-capability filtering for agent use.
  - Two-tier caching (memory + disk) for model discovery results.
  - Configurable ``num_ctx`` and remote auth (Bearer token).
  - DeepSeek R1 reasoning detection (``<think>`` blocks).
  - Default model configuration for ``devstral:24b``.

The provider still uses the OpenAI-compatible ``/v1/chat/completions``
endpoint for chat (since Ollama's native ``/api/chat`` does not return
OpenAI-style tool_calls), but uses the native Ollama API/SDK for model
discovery and metadata.
"""

import json
import logging
import os
import re
import tempfile
import time
from typing import Any

import requests

from ai.providers.base import BaseProvider, LLMResponse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ollama SDK -- optional, with graceful degradation to HTTP
# ---------------------------------------------------------------------------

OLLAMA_SDK_AVAILABLE = False
try:
    import ollama as _ollama_sdk
    OLLAMA_SDK_AVAILABLE = True
except ImportError:
    _ollama_sdk = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"

# TASK-038: Cache TTL for is_available() in seconds
_AVAILABLE_CACHE_TTL = 30

# Model discovery cache TTL (memory) -- 5 minutes
_MODEL_CACHE_TTL = 300

# Disk cache location (relative to project root)
_DISK_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data")
_DISK_CACHE_FILE = os.path.join(_DISK_CACHE_DIR, "ollama_models_cache.json")

# ---------------------------------------------------------------------------
# Default Model Configuration
# ---------------------------------------------------------------------------

OLLAMA_DEFAULT_MODEL_ID = "devstral:24b"

OLLAMA_DEFAULT_MODEL_INFO: dict[str, Any] = {
    "max_tokens": 4096,
    "context_window": 200000,
    "supports_images": True,
    "supports_tools": True,
    "input_price": 0,
    "output_price": 0,
}

# DeepSeek R1 default temperature
_DEEPSEEK_R1_TEMPERATURE = 0.6

# Regex to detect <think>...</think> blocks in streaming output
_THINK_BLOCK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)


class OllamaProvider(BaseProvider):
    """LLM provider backed by a local or remote Ollama instance.

    Supports native ``ollama`` Python SDK for model discovery with fallback
    to raw HTTP requests when the SDK is not installed.
    """

    def __init__(self):
        self._base_url: str = DEFAULT_OLLAMA_BASE_URL
        self._timeout: int = 300  # 5 minutes default for large models
        self._api_key: str | None = None
        self._num_ctx: int | None = None

        # TASK-038: Availability cache
        self._available_cache: bool | None = None
        self._available_cache_time: float = 0

        # Two-tier model cache
        self._model_cache: list[dict] | None = None
        self._model_cache_time: float = 0

        # SDK client (created on configure if SDK available)
        self._sdk_client: Any | None = None

    # -- BaseProvider properties -------------------------------------------

    @property
    def name(self) -> str:
        return "Ollama"

    @property
    def provider_type(self) -> str:
        return "ollama"

    # -- Configuration -----------------------------------------------------

    def configure(self, base_url: str = "", timeout: int = 0, **kwargs):
        self._base_url = base_url.rstrip("/") if base_url else DEFAULT_OLLAMA_BASE_URL
        if timeout > 0:
            self._timeout = timeout

        self._api_key = kwargs.get("api_key") or None
        self._num_ctx = kwargs.get("num_ctx") or None
        if self._num_ctx is not None:
            self._num_ctx = int(self._num_ctx)

        # Invalidate caches on reconfigure
        self._model_cache = None
        self._model_cache_time = 0
        self._available_cache = None
        self._available_cache_time = 0

        # Create SDK client if available
        self._sdk_client = None
        if OLLAMA_SDK_AVAILABLE:
            try:
                sdk_kwargs: dict[str, Any] = {"host": self._base_url}
                # The ollama SDK's Client accepts headers for auth
                if self._api_key:
                    sdk_kwargs["headers"] = {"Authorization": f"Bearer {self._api_key}"}
                self._sdk_client = _ollama_sdk.Client(**sdk_kwargs)
            except Exception as exc:
                logger.warning("Failed to create Ollama SDK client: %s", exc)
                self._sdk_client = None

    def is_available(self) -> bool:
        """Check if Ollama is reachable.

        TASK-038: Results are cached for 30 seconds to avoid blocking
        network calls on every invocation.
        """
        now = time.time()
        if self._available_cache is not None and (now - self._available_cache_time) < _AVAILABLE_CACHE_TTL:
            return self._available_cache
        try:
            resp = requests.get(
                f"{self._base_url}/api/tags",
                timeout=3,
                headers=self._auth_headers(),
            )
            result = resp.status_code == 200
        except Exception:
            result = False
        self._available_cache = result
        self._available_cache_time = now
        return result

    # -- Auth helpers ------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        """Return Authorization header dict if an API key is configured."""
        if self._api_key:
            return {"Authorization": f"Bearer {self._api_key}"}
        return {}

    # -- Message creation --------------------------------------------------

    def create_message(self, messages, system, tools, max_tokens, model) -> LLMResponse:
        """Call Ollama's OpenAI-compatible endpoint (non-streaming)."""
        openai_messages = self._convert_messages(messages, system, model=model)
        openai_tools = self._convert_tools(tools)

        payload: dict = {
            "model": model,
            "messages": openai_messages,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if openai_tools:
            payload["tools"] = openai_tools

        # Configurable num_ctx
        if self._num_ctx is not None:
            payload.setdefault("options", {})["num_ctx"] = self._num_ctx

        # DeepSeek R1 temperature
        if self._is_deepseek_r1(model):
            payload["temperature"] = _DEEPSEEK_R1_TEMPERATURE

        try:
            resp = requests.post(
                f"{self._base_url}/v1/chat/completions",
                json=payload,
                timeout=self._timeout,
                headers=self._auth_headers(),
            )
            resp.raise_for_status()
        except requests.HTTPError as http_err:
            status = http_err.response.status_code if http_err.response is not None else None
            if status == 404:
                raise RuntimeError(
                    f"Ollama returned HTTP 404: Model not found. "
                    f"Run 'ollama pull <model_name>' to download the model first."
                ) from http_err
            raise RuntimeError(
                f"Ollama HTTP error {status}: "
                f"{http_err.response.text[:200] if http_err.response is not None else str(http_err)}"
            ) from http_err
        return self._convert_response(resp.json())

    def stream_message(self, messages, system, tools, max_tokens, model,
                       text_callback=None) -> LLMResponse:
        """Stream from Ollama's OpenAI-compatible endpoint.

        For DeepSeek R1 models, ``<think>...</think>`` blocks in the
        streamed text are detected and classified as reasoning content.
        """
        openai_messages = self._convert_messages(messages, system, model=model)
        openai_tools = self._convert_tools(tools)

        payload: dict = {
            "model": model,
            "messages": openai_messages,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if openai_tools:
            payload["tools"] = openai_tools

        # Configurable num_ctx
        if self._num_ctx is not None:
            payload.setdefault("options", {})["num_ctx"] = self._num_ctx

        # DeepSeek R1 temperature
        is_r1 = self._is_deepseek_r1(model)
        if is_r1:
            payload["temperature"] = _DEEPSEEK_R1_TEMPERATURE

        accumulated_text = ""
        tool_calls_data: list[dict] = []
        finish_reason = ""
        usage_data = {"input_tokens": 0, "output_tokens": 0}
        model_name = model

        try:
            resp = requests.post(
                f"{self._base_url}/v1/chat/completions",
                json=payload,
                timeout=self._timeout,
                stream=True,
                headers=self._auth_headers(),
            )
            resp.raise_for_status()

            for line in resp.iter_lines():
                if not line:
                    continue
                line_str = line.decode("utf-8")
                if line_str.startswith("data: "):
                    line_str = line_str[6:]
                if line_str.strip() == "[DONE]":
                    break

                try:
                    chunk = json.loads(line_str)
                except json.JSONDecodeError:
                    continue

                if "model" in chunk:
                    model_name = chunk["model"]

                choices = chunk.get("choices", [])
                if choices:
                    choice = choices[0]
                    delta = choice.get("delta", {})

                    # Text content
                    if "content" in delta and delta["content"]:
                        text = delta["content"]
                        accumulated_text += text
                        if text_callback:
                            text_callback(text)

                    # Tool calls (streamed as deltas)
                    if "tool_calls" in delta:
                        for tc_delta in delta["tool_calls"]:
                            idx = tc_delta.get("index", 0)
                            while len(tool_calls_data) <= idx:
                                tool_calls_data.append(
                                    {"id": "", "name": "", "arguments": ""}
                                )
                            if "id" in tc_delta:
                                tool_calls_data[idx]["id"] = tc_delta["id"]
                            if "function" in tc_delta:
                                func = tc_delta["function"]
                                if "name" in func:
                                    tool_calls_data[idx]["name"] = func["name"]
                                if "arguments" in func:
                                    tool_calls_data[idx]["arguments"] += func[
                                        "arguments"
                                    ]

                    if "finish_reason" in choice and choice["finish_reason"]:
                        finish_reason = choice["finish_reason"]

                # Usage from the final chunk
                if "usage" in chunk and chunk["usage"]:
                    u = chunk["usage"]
                    usage_data["input_tokens"] = u.get("prompt_tokens", 0)
                    usage_data["output_tokens"] = u.get("completion_tokens", 0)

        except requests.HTTPError as http_err:
            status = http_err.response.status_code if http_err.response is not None else None
            if status == 404:
                raise RuntimeError(
                    f"Ollama returned HTTP 404: Model not found. "
                    f"Run 'ollama pull <model_name>' to download the model first."
                ) from http_err
            raise RuntimeError(
                f"Ollama HTTP error {status}: "
                f"{http_err.response.text[:200] if http_err.response is not None else str(http_err)}"
            ) from http_err
        except (requests.RequestException, ConnectionError, TimeoutError) as exc:
            # TASK-044: Narrow exception types for streaming fallback
            logger.warning("Streaming failed, trying sync with short timeout: %s", exc)
            saved_timeout = self._timeout
            self._timeout = min(30, saved_timeout)
            try:
                return self.create_message(messages, system, tools, max_tokens, model)
            finally:
                self._timeout = saved_timeout

        # Build the final LLMResponse
        result = LLMResponse()
        result.model = model_name
        result.usage = usage_data

        # DeepSeek R1: parse <think> blocks as reasoning content
        if is_r1 and accumulated_text:
            result.content = self._parse_r1_content(accumulated_text)
        else:
            if accumulated_text:
                result.content.append({"type": "text", "text": accumulated_text})

        for tc in tool_calls_data:
            if tc["name"]:
                try:
                    args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                except json.JSONDecodeError:
                    args = {}
                result.content.append({
                    "type": "tool_use",
                    "id": tc.get("id", f"call_{tc['name']}"),
                    "name": tc["name"],
                    "input": args,
                })

        # Determine stop reason
        if tool_calls_data and any(tc["name"] for tc in tool_calls_data):
            result.stop_reason = "tool_use"
        elif finish_reason == "stop":
            result.stop_reason = "end_turn"
        elif finish_reason == "length":
            result.stop_reason = "max_tokens"
        else:
            result.stop_reason = "end_turn"

        return result

    # -- Model listing & discovery -----------------------------------------

    def list_models(self, tool_capable_only: bool = False) -> list[dict]:
        """Fetch installed models with detailed metadata.

        Uses a two-tier cache (memory -> disk -> API) to avoid redundant
        network calls.

        Args:
            tool_capable_only: If True, only return models that support
                tool calling.  Non-tool-capable models are still accessible
                via ``list_models(tool_capable_only=False)``.

        Returns:
            List of model info dicts with keys: ``id``, ``name``, ``size``,
            ``modified``, ``context_length``, ``supports_tools``,
            ``supports_vision``, ``parameter_size``, ``family``,
            ``description``.
        """
        models = self._get_models_cached()

        if tool_capable_only:
            models = [m for m in models if m.get("supports_tools", False)]

        return models

    def _get_models_cached(self) -> list[dict]:
        """Return model list from memory cache, disk cache, or API (in order)."""
        now = time.time()

        # 1. Memory cache
        if self._model_cache is not None and (now - self._model_cache_time) < _MODEL_CACHE_TTL:
            return list(self._model_cache)

        # 2. Disk cache
        disk_models = self._read_disk_cache()
        if disk_models is not None:
            self._model_cache = disk_models
            self._model_cache_time = now
            # Refresh from API in background would be ideal, but for
            # simplicity we just check if disk cache is stale
            disk_age = self._disk_cache_age()
            if disk_age is not None and disk_age < _MODEL_CACHE_TTL:
                return list(self._model_cache)

        # 3. API discovery (two-phase)
        try:
            api_models = self._discover_models()
            if api_models:  # Don't overwrite good cache with empty response
                self._model_cache = api_models
                self._model_cache_time = now
                self._write_disk_cache(api_models)
                return list(api_models)
        except Exception as exc:
            logger.warning("Failed to discover Ollama models: %s", exc)

        # Fallback to whatever we have cached
        if self._model_cache is not None:
            return list(self._model_cache)
        if disk_models is not None:
            return list(disk_models)
        return []

    def _discover_models(self) -> list[dict]:
        """Two-phase model discovery: list then show each model.

        Phase 1: GET /api/tags -- list all installed models.
        Phase 2: POST /api/show -- fetch detailed metadata per model.
        """
        # Phase 1: List models
        raw_models = self._list_models_raw()
        if not raw_models:
            return []

        # Phase 2: Enrich with metadata
        result: list[dict] = []
        for m in raw_models:
            model_id = m.get("name", "")
            if not model_id:
                continue

            entry: dict[str, Any] = {
                "id": model_id,
                "name": model_id,
                "size": m.get("size", 0),
                "modified": m.get("modified_at", ""),
            }

            # Fetch detailed metadata via /api/show
            meta = self._show_model(model_id)
            if meta:
                # Extract model_info fields
                model_info = meta.get("model_info", {})
                details = meta.get("details", {})
                capabilities = meta.get("capabilities", [])

                # Context length from model_info
                # Try common keys for context length
                ctx_len = None
                for key, val in model_info.items():
                    if "context_length" in key.lower():
                        ctx_len = val
                        break
                entry["context_length"] = ctx_len

                # Tool calling and vision from capabilities
                entry["supports_tools"] = "tools" in capabilities
                entry["supports_vision"] = "vision" in capabilities

                # Parameter size and family from details
                entry["parameter_size"] = details.get("parameter_size", "")
                entry["family"] = details.get("family", "")

                # Build description
                desc_parts = []
                if entry["family"]:
                    desc_parts.append(entry["family"])
                if entry["parameter_size"]:
                    desc_parts.append(entry["parameter_size"])
                caps = []
                if entry["supports_tools"]:
                    caps.append("tools")
                if entry["supports_vision"]:
                    caps.append("vision")
                if caps:
                    desc_parts.append(f"[{', '.join(caps)}]")
                entry["description"] = " - ".join(desc_parts) if desc_parts else model_id
            else:
                # Fallback: no detailed metadata available
                entry["context_length"] = None
                entry["supports_tools"] = False
                entry["supports_vision"] = False
                entry["parameter_size"] = ""
                entry["family"] = ""
                entry["description"] = model_id

            result.append(entry)

        return result

    def _list_models_raw(self) -> list[dict]:
        """Phase 1: List all installed models via SDK or HTTP."""
        if self._sdk_client is not None:
            try:
                resp = self._sdk_client.list()
                # SDK returns a ListResponse with .models attribute
                models_list = getattr(resp, "models", None)
                if models_list is None:
                    # Older SDK versions may return a dict
                    if isinstance(resp, dict):
                        models_list = resp.get("models", [])
                    else:
                        models_list = []
                result = []
                for m in models_list:
                    if hasattr(m, "model"):
                        # SDK model object
                        result.append({
                            "name": getattr(m, "model", ""),
                            "size": getattr(m, "size", 0),
                            "modified_at": str(getattr(m, "modified_at", "")),
                        })
                    elif isinstance(m, dict):
                        result.append(m)
                    else:
                        result.append({"name": str(m)})
                return result
            except Exception as exc:
                logger.debug("SDK list() failed, falling back to HTTP: %s", exc)

        # HTTP fallback
        try:
            resp = requests.get(
                f"{self._base_url}/api/tags",
                timeout=5,
                headers=self._auth_headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("models", [])
        except Exception as exc:
            logger.warning("Failed to list Ollama models via HTTP: %s", exc)
            return []

    def _show_model(self, model_id: str) -> dict | None:
        """Phase 2: Fetch detailed metadata for a single model via SDK or HTTP."""
        if self._sdk_client is not None:
            try:
                resp = self._sdk_client.show(model_id)
                # SDK returns a ShowResponse object; convert to dict
                if hasattr(resp, "model_dump"):
                    return resp.model_dump()
                elif isinstance(resp, dict):
                    return resp
                else:
                    # Try to extract known attributes
                    return {
                        "model_info": getattr(resp, "model_info", {}),
                        "details": getattr(resp, "details", {}),
                        "capabilities": getattr(resp, "capabilities", []),
                    }
            except Exception as exc:
                logger.debug("SDK show(%s) failed, falling back to HTTP: %s", model_id, exc)

        # HTTP fallback
        try:
            resp = requests.post(
                f"{self._base_url}/api/show",
                json={"name": model_id},
                timeout=10,
                headers=self._auth_headers(),
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.debug("Failed to show model %s via HTTP: %s", model_id, exc)
            return None

    # -- Two-tier cache (disk) ---------------------------------------------

    def _read_disk_cache(self) -> list[dict] | None:
        """Read the disk cache file, returning None if missing or corrupt."""
        cache_path = _DISK_CACHE_FILE
        try:
            if not os.path.exists(cache_path):
                return None
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list) and data:
                return data
            return None
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug("Failed to read disk cache: %s", exc)
            return None

    def _write_disk_cache(self, models: list[dict]) -> None:
        """Atomically write model list to disk cache."""
        cache_path = _DISK_CACHE_FILE
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            # Atomic write: write to temp file, then rename
            fd, tmp_path = tempfile.mkstemp(
                dir=os.path.dirname(cache_path),
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(models, f, indent=2, default=str)
                # On Windows, os.rename fails if target exists; use replace
                os.replace(tmp_path, cache_path)
            except Exception:
                # Clean up temp file on failure
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except OSError as exc:
            logger.debug("Failed to write disk cache: %s", exc)

    def _disk_cache_age(self) -> float | None:
        """Return the age of the disk cache in seconds, or None if missing."""
        try:
            if os.path.exists(_DISK_CACHE_FILE):
                return time.time() - os.path.getmtime(_DISK_CACHE_FILE)
        except OSError:
            pass
        return None

    # -- DeepSeek R1 helpers -----------------------------------------------

    @staticmethod
    def _is_deepseek_r1(model: str) -> bool:
        """Check if the model is a DeepSeek R1 variant."""
        return "deepseek-r1" in model.lower()

    @staticmethod
    def _parse_r1_content(text: str) -> list[dict]:
        """Parse DeepSeek R1 output, separating ``<think>`` blocks.

        Returns a list of content blocks where ``<think>`` regions are
        classified as ``"reasoning"`` type and everything else as ``"text"``.
        """
        blocks: list[dict] = []
        last_end = 0

        for match in _THINK_BLOCK_RE.finditer(text):
            # Text before the think block
            before = text[last_end:match.start()]
            if before.strip():
                blocks.append({"type": "text", "text": before})

            # The reasoning block
            reasoning = match.group(1).strip()
            if reasoning:
                blocks.append({"type": "reasoning", "text": reasoning})

            last_end = match.end()

        # Remaining text after last think block
        after = text[last_end:]
        if after.strip():
            blocks.append({"type": "text", "text": after})

        # If no think blocks found, return as plain text
        if not blocks:
            blocks.append({"type": "text", "text": text})

        return blocks

    # ======================================================================
    # Internal: message format conversion (Anthropic -> OpenAI)
    # ======================================================================

    def _model_has_vision(self, model: str) -> bool:
        """Check if the given model supports vision (image inputs).

        TASK-100: Uses the cached model metadata from discovery.  Falls back
        to the default model info for the configured default model.
        """
        # Check cached model list first
        if self._model_cache:
            for m in self._model_cache:
                if m.get("id") == model or m.get("name") == model:
                    return bool(m.get("supports_vision", False))

        # Fallback: check default model info
        if model == OLLAMA_DEFAULT_MODEL_ID:
            return bool(OLLAMA_DEFAULT_MODEL_INFO.get("supports_images", False))

        return False

    def _convert_messages(self, messages: list, system: str, *, model: str = "") -> list:
        """Convert Anthropic-format messages to OpenAI chat format.

        TASK-100: When *model* is vision-capable, image content blocks are
        converted to OpenAI ``image_url`` format instead of being replaced
        with a text placeholder.
        """
        openai_msgs: list[dict] = []
        has_vision = self._model_has_vision(model) if model else False

        if system:
            openai_msgs.append({"role": "system", "content": system})

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if isinstance(content, str):
                openai_msgs.append({"role": role, "content": content})

            elif isinstance(content, list):
                text_parts: list[str] = []
                image_parts: list[dict] = []  # TASK-100: collected image blocks
                tool_results: list[dict] = []
                tool_calls: list[dict] = []

                for block in content:
                    btype = block.get("type")

                    if btype == "text":
                        text_parts.append(block["text"])

                    elif btype == "image":
                        if has_vision:
                            # TASK-100: Include base64 image in OpenAI vision format
                            source = block.get("source", {})
                            image_data = source.get("data", "")
                            media_type = source.get("media_type", "image/png")
                            if image_data:
                                image_parts.append({
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:{media_type};base64,{image_data}",
                                    },
                                })
                            else:
                                text_parts.append("[Image: screenshot from Fusion 360]")
                        else:
                            # Non-vision model -- convert to a text placeholder
                            text_parts.append("[Image: screenshot from Fusion 360]")

                    elif btype == "tool_use":
                        tool_calls.append({
                            "id": block.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": block["name"],
                                "arguments": json.dumps(block.get("input", {})),
                            },
                        })

                    elif btype == "tool_result":
                        tool_result_content = block.get("content", "")
                        if isinstance(tool_result_content, list):
                            tool_result_content = " ".join(
                                b.get("text", "")
                                for b in tool_result_content
                                if b.get("type") == "text"
                            )
                        if not isinstance(tool_result_content, str):
                            tool_result_content = json.dumps(tool_result_content)
                        tool_results.append({
                            "tool_call_id": block.get("tool_use_id", ""),
                            "content": tool_result_content,
                        })

                if role == "assistant":
                    msg_dict: dict = {"role": "assistant"}
                    if text_parts:
                        msg_dict["content"] = "\n".join(text_parts)
                    if tool_calls:
                        msg_dict["tool_calls"] = tool_calls
                        if not msg_dict.get("content"):
                            msg_dict["content"] = None
                    openai_msgs.append(msg_dict)

                elif role == "user":
                    # Tool results become separate ``role: tool`` messages
                    for tr in tool_results:
                        openai_msgs.append({
                            "role": "tool",
                            "tool_call_id": tr["tool_call_id"],
                            "content": tr["content"],
                        })
                    if text_parts or image_parts:
                        # TASK-100: Use content array when images are present
                        if image_parts:
                            content_blocks: list[dict] = []
                            if text_parts:
                                content_blocks.append({
                                    "type": "text",
                                    "text": "\n".join(text_parts),
                                })
                            content_blocks.extend(image_parts)
                            openai_msgs.append({
                                "role": "user",
                                "content": content_blocks,
                            })
                        else:
                            openai_msgs.append({
                                "role": "user",
                                "content": "\n".join(text_parts),
                            })
            else:
                openai_msgs.append({"role": role, "content": str(content)})

        return openai_msgs

    def _convert_tools(self, tools: list) -> list:
        """Convert Anthropic tool definitions to OpenAI function-calling format."""
        openai_tools: list[dict] = []
        for tool in tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get(
                        "input_schema", {"type": "object", "properties": {}}
                    ),
                },
            })
        return openai_tools

    def _convert_response(self, data: dict) -> LLMResponse:
        """Convert an OpenAI-format response dict to ``LLMResponse``."""
        result = LLMResponse()
        result.model = data.get("model", "")

        usage = data.get("usage", {})
        result.usage = {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        }

        choices = data.get("choices", [])
        if choices:
            choice = choices[0]
            message = choice.get("message", {})

            if message.get("content"):
                # DeepSeek R1 reasoning detection for sync responses
                model_name = data.get("model", "")
                if self._is_deepseek_r1(model_name):
                    result.content = self._parse_r1_content(message["content"])
                else:
                    result.content.append({"type": "text", "text": message["content"]})

            for tc in message.get("tool_calls", []):
                func = tc.get("function", {})
                try:
                    args = json.loads(func.get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}
                result.content.append({
                    "type": "tool_use",
                    "id": tc.get("id", f"call_{func.get('name', '')}"),
                    "name": func.get("name", ""),
                    "input": args,
                })

            finish = choice.get("finish_reason", "")
            if message.get("tool_calls"):
                result.stop_reason = "tool_use"
            elif finish == "stop":
                result.stop_reason = "end_turn"
            elif finish == "length":
                result.stop_reason = "max_tokens"
            else:
                result.stop_reason = "end_turn"

        return result
