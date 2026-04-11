"""Ollama local LLM provider using the OpenAI-compatible API.

Ollama exposes ``/v1/chat/completions`` which accepts the OpenAI function-
calling format.  Models that support tool calling include ``llama3.1``,
``qwen2.5``, ``mistral``, among others.

No additional SDK dependency is required -- we use the ``requests`` library
that is already in the project's requirements.
"""

import json
import logging

import requests

from ai.providers.base import BaseProvider, LLMResponse

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"


class OllamaProvider(BaseProvider):
    """LLM provider backed by a local Ollama instance."""

    def __init__(self):
        self._base_url: str = DEFAULT_OLLAMA_BASE_URL

    # -- BaseProvider properties -------------------------------------------

    @property
    def name(self) -> str:
        return "Ollama"

    @property
    def provider_type(self) -> str:
        return "ollama"

    # -- Configuration -----------------------------------------------------

    def configure(self, base_url: str = "", **kwargs):
        self._base_url = base_url.rstrip("/") if base_url else DEFAULT_OLLAMA_BASE_URL

    def is_available(self) -> bool:
        try:
            resp = requests.get(f"{self._base_url}/api/tags", timeout=3)
            return resp.status_code == 200
        except Exception:
            return False

    # -- Message creation --------------------------------------------------

    def create_message(self, messages, system, tools, max_tokens, model) -> LLMResponse:
        """Call Ollama's OpenAI-compatible endpoint (non-streaming)."""
        openai_messages = self._convert_messages(messages, system)
        openai_tools = self._convert_tools(tools)

        payload: dict = {
            "model": model,
            "messages": openai_messages,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if openai_tools:
            payload["tools"] = openai_tools

        resp = requests.post(
            f"{self._base_url}/v1/chat/completions",
            json=payload,
            timeout=120,
        )
        resp.raise_for_status()
        return self._convert_response(resp.json())

    def stream_message(self, messages, system, tools, max_tokens, model,
                       text_callback=None) -> LLMResponse:
        """Stream from Ollama's OpenAI-compatible endpoint."""
        openai_messages = self._convert_messages(messages, system)
        openai_tools = self._convert_tools(tools)

        payload: dict = {
            "model": model,
            "messages": openai_messages,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if openai_tools:
            payload["tools"] = openai_tools

        accumulated_text = ""
        tool_calls_data: list[dict] = []
        finish_reason = ""
        usage_data = {"input_tokens": 0, "output_tokens": 0}
        model_name = model

        try:
            resp = requests.post(
                f"{self._base_url}/v1/chat/completions",
                json=payload,
                timeout=120,
                stream=True,
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

        except Exception as exc:
            logger.warning("Streaming failed, falling back to sync: %s", exc)
            return self.create_message(messages, system, tools, max_tokens, model)

        # Build the final LLMResponse
        result = LLMResponse()
        result.model = model_name
        result.usage = usage_data

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

    # -- Model listing -----------------------------------------------------

    def list_models(self) -> list[dict]:
        """Fetch the installed model list from the Ollama API."""
        try:
            resp = requests.get(f"{self._base_url}/api/tags", timeout=5)
            resp.raise_for_status()
            data = resp.json()
            models = []
            for m in data.get("models", []):
                models.append({
                    "id": m.get("name", ""),
                    "name": m.get("name", ""),
                    "size": m.get("size", 0),
                    "modified": m.get("modified_at", ""),
                })
            return models
        except Exception as exc:
            logger.warning("Failed to list Ollama models: %s", exc)
            return []

    # ======================================================================
    # Internal: message format conversion (Anthropic -> OpenAI)
    # ======================================================================

    def _convert_messages(self, messages: list, system: str) -> list:
        """Convert Anthropic-format messages to OpenAI chat format."""
        openai_msgs: list[dict] = []

        if system:
            openai_msgs.append({"role": "system", "content": system})

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if isinstance(content, str):
                openai_msgs.append({"role": role, "content": content})

            elif isinstance(content, list):
                text_parts: list[str] = []
                tool_results: list[dict] = []
                tool_calls: list[dict] = []

                for block in content:
                    btype = block.get("type")

                    if btype == "text":
                        text_parts.append(block["text"])

                    elif btype == "image":
                        # Ollama's OpenAI-compat API generally does not
                        # support inline images -- convert to a text note.
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
                    if text_parts:
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
