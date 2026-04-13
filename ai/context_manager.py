"""
ai/context_manager.py
Context management and conversation condensation for long CAD sessions.

When conversation history approaches the model's context window limit,
this module summarises older messages while preserving recent turns and
critical design state.  It can optionally use an LLM call for high-quality
summaries and falls back to rule-based extraction when that is unavailable.
"""
import logging
import json
from typing import Optional

logger = logging.getLogger(__name__)

# Approximate context windows per model
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-sonnet-4-20250514": 200_000,
    "claude-opus-4-20250514": 200_000,
    "claude-3-5-sonnet-20241022": 200_000,
    "claude-3-opus-20240229": 200_000,
    "claude-3-haiku-20240307": 200_000,
}

DEFAULT_CONTEXT_WINDOW: int = 200_000
CONDENSE_THRESHOLD: float = 0.65  # Trigger at 65 % of context window
CHARS_PER_TOKEN: int = 4  # Rough character-to-token ratio

# Number of recent *turns* to always preserve (user + assistant = 1 turn each)
PRESERVE_RECENT_TURNS: int = 4


class ContextManager:
    """Manages conversation context to prevent overflow."""

    def __init__(self, model: str = "claude-sonnet-4-20250514"):
        self.model = model
        self._context_window = MODEL_CONTEXT_WINDOWS.get(model, DEFAULT_CONTEXT_WINDOW)
        self._threshold = int(self._context_window * CONDENSE_THRESHOLD)
        self._condensation_count: int = 0

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def update_model(self, model: str) -> None:
        """Update the target model and recalculate threshold."""
        self.model = model
        self._context_window = MODEL_CONTEXT_WINDOWS.get(model, DEFAULT_CONTEXT_WINDOW)
        self._threshold = int(self._context_window * CONDENSE_THRESHOLD)

    def reset(self) -> None:
        """Reset condensation counter (e.g. on new conversation)."""
        self._condensation_count = 0

    # ------------------------------------------------------------------
    # Token estimation
    # ------------------------------------------------------------------

    def estimate_tokens(self, messages: list, system_prompt: str = "") -> int:
        """Estimate total token count for *messages* + *system_prompt*."""
        total_chars = len(system_prompt)
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for block in content:
                    btype = block.get("type")
                    if btype == "text":
                        total_chars += len(block.get("text", ""))
                    elif btype == "image":
                        # Base64 images: ~1 token per 3 chars of base64
                        data = block.get("source", {}).get("data", "")
                        total_chars += len(data) // 3
                    elif btype == "tool_use":
                        total_chars += len(json.dumps(block.get("input", {})))
                        total_chars += len(block.get("name", ""))
                    elif btype == "tool_result":
                        content_val = block.get("content", "")
                        if isinstance(content_val, str):
                            total_chars += len(content_val)
                        elif isinstance(content_val, list):
                            for sub in content_val:
                                if sub.get("type") == "text":
                                    total_chars += len(sub.get("text", ""))
                                elif sub.get("type") == "image":
                                    total_chars += (
                                        len(sub.get("source", {}).get("data", "")) // 3
                                    )
        return total_chars // CHARS_PER_TOKEN

    # ------------------------------------------------------------------
    # Threshold check
    # ------------------------------------------------------------------

    def should_condense(self, messages: list, system_prompt: str = "") -> bool:
        """Return ``True`` when the conversation needs condensation."""
        estimated = self.estimate_tokens(messages, system_prompt)
        return estimated > self._threshold

    # ------------------------------------------------------------------
    # Condensation
    # ------------------------------------------------------------------

    @staticmethod
    def _find_safe_split_point(messages: list, intended_index: int) -> int:
        """Find a split index that does not break tool_use / tool_result pairs.

        The Anthropic API requires that every ``assistant`` message containing
        ``tool_use`` blocks is immediately followed by a ``user`` message with
        the corresponding ``tool_result`` blocks.  If *intended_index* would
        land on such a ``user(tool_result)`` message we walk backwards until
        we find a safe boundary.

        Returns the adjusted (potentially earlier) split index.
        """
        idx = intended_index
        while idx > 0:
            msg = messages[idx]
            # Check if this message is a user message containing tool_result blocks
            if msg.get("role") == "user":
                content = msg.get("content")
                if isinstance(content, list):
                    has_tool_result = any(
                        isinstance(block, dict) and block.get("type") == "tool_result"
                        for block in content
                    )
                    if has_tool_result:
                        # Move back by 1 to keep the preceding assistant(tool_use) with its result
                        idx -= 1
                        continue
            # Safe boundary found
            break
        return idx

    def condense(self, messages: list, client=None,
                 design_state_summary: str = None) -> list:
        """Condense conversation history by summarising older messages.

        Strategy:
        1. Split messages into *old* (to condense) and *recent* (to keep).
        2. If a Claude *client* is available, use it for an LLM summary.
        3. Otherwise fall back to rule-based extraction.
        4. If *design_state_summary* is provided, append it so the current
           design state survives condensation.
        5. Return ``[summary_message] + recent_messages``.

        Parameters:
            messages: The full conversation message list.
            client:   Optional LLM client for high-quality summaries.
            design_state_summary: Optional compact design state string
                to preserve across condensation (from DesignStateTracker).
        """
        if len(messages) <= PRESERVE_RECENT_TURNS * 2:
            # Too few messages to condense -- fall back to truncation
            return self._truncate(messages)

        recent_count = PRESERVE_RECENT_TURNS * 2  # user + assistant per turn
        intended_split = len(messages) - recent_count
        safe_split = self._find_safe_split_point(messages, intended_split)
        old_messages = messages[:safe_split]
        recent_messages = messages[safe_split:]

        # Try LLM-based summarisation if a client is provided
        summary: Optional[str] = None
        if client:
            try:
                summary = self._llm_summarize(old_messages, client)
            except Exception as exc:
                logger.warning("LLM condensation failed: %s", exc)

        # Fallback to rule-based summarisation
        if not summary:
            summary = self._rule_based_summarize(old_messages)

        # Append design state so it survives condensation
        if design_state_summary:
            summary += (
                "\n\n--- Current Design State ---\n"
                + design_state_summary
            )

        self._condensation_count += 1

        summary_message = {
            "role": "user",
            "content": (
                f"[Context Summary - Condensation #{self._condensation_count}]\n\n"
                f"{summary}\n\n"
                "[End of summary. The conversation continues below with the "
                "most recent messages.]"
            ),
        }

        return [summary_message] + recent_messages

    # ------------------------------------------------------------------
    # Truncation fallback
    # ------------------------------------------------------------------

    def _truncate(self, messages: list) -> list:
        """Simple truncation: remove oldest messages, keeping recent ones."""
        if len(messages) <= 4:
            return messages
        half = len(messages) // 2
        return messages[-half:]

    # ------------------------------------------------------------------
    # LLM-based summarisation
    # ------------------------------------------------------------------

    def _llm_summarize(self, old_messages: list, client) -> Optional[str]:
        """Use Claude to summarise old conversation (separate API call)."""
        condensed_text = self._messages_to_text(old_messages)

        summary_prompt = (
            "Summarize this CAD design session conversation. Preserve:\n"
            "1. What was created (bodies, sketches, components -- names and dimensions)\n"
            "2. Key design decisions and reasoning\n"
            "3. Errors encountered and how they were resolved\n"
            "4. Current design state (what exists in the model)\n"
            "5. Any pending tasks or user requests not yet fulfilled\n\n"
            "Be concise but thorough. Use bullet points. Include specific "
            "dimensions and names."
        )

        # Access the underlying anthropic client
        if hasattr(client, "client") and client.client:
            response = client.client.messages.create(
                model=client.model,
                max_tokens=1024,
                messages=[
                    {
                        "role": "user",
                        "content": f"{summary_prompt}\n\n---\n\n{condensed_text}",
                    }
                ],
            )
            if response.content:
                return response.content[0].text

        return None

    # ------------------------------------------------------------------
    # Rule-based summarisation
    # ------------------------------------------------------------------

    def _rule_based_summarize(self, old_messages: list) -> str:
        """Extract key facts from messages without an LLM call."""
        summary_parts: list[str] = []

        tool_calls: list[dict] = []
        user_requests: list[str] = []
        errors: list[str] = []
        tool_results: list[str] = []

        for msg in old_messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "user":
                if isinstance(content, str):
                    if not content.startswith("[Auto-screenshot"):
                        user_requests.append(content[:200])
                elif isinstance(content, list):
                    for block in content:
                        if block.get("type") == "text":
                            text = block["text"]
                            if not text.startswith(
                                "[Auto-screenshot"
                            ) and not text.startswith("[Context Summary"):
                                user_requests.append(text[:200])
                        elif block.get("type") == "tool_result":
                            result_content = block.get("content", "")
                            if isinstance(result_content, str):
                                try:
                                    parsed = json.loads(result_content)
                                    if not parsed.get("success", True):
                                        errors.append(
                                            f"{block.get('tool_use_id', '?')}: "
                                            f"{parsed.get('error', 'Unknown error')}"
                                        )
                                    tool_results.append(
                                        self._summarize_tool_result(parsed)
                                    )
                                except json.JSONDecodeError:
                                    tool_results.append(result_content[:100])

            elif role == "assistant":
                if isinstance(content, list):
                    for block in content:
                        if block.get("type") == "tool_use":
                            tool_calls.append(
                                {
                                    "name": block.get("name", ""),
                                    "input": self._summarize_tool_input(
                                        block.get("input", {})
                                    ),
                                }
                            )

        # --- Build summary ---
        if user_requests:
            summary_parts.append("## User Requests")
            for req in user_requests[-5:]:
                summary_parts.append(f"- {req}")

        if tool_calls:
            summary_parts.append("\n## Operations Performed")
            tool_counts: dict[str, int] = {}
            for tc in tool_calls:
                name = tc["name"]
                tool_counts[name] = tool_counts.get(name, 0) + 1
            for name, count in sorted(tool_counts.items()):
                summary_parts.append(f"- {name}: called {count} time(s)")

            summary_parts.append("\n### Recent operations:")
            for tc in tool_calls[-8:]:
                summary_parts.append(f"- {tc['name']}({tc['input']})")

        if errors:
            summary_parts.append("\n## Errors Encountered")
            for err in errors[-5:]:
                summary_parts.append(f"- {err}")

        if tool_results:
            for tr in reversed(tool_results):
                if "bodies" in str(tr) or "count" in str(tr):
                    summary_parts.append(f"\n## Last Known Design State\n{tr}")
                    break

        return (
            "\n".join(summary_parts)
            if summary_parts
            else "Previous conversation condensed. No specific design state preserved."
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _messages_to_text(self, messages: list) -> str:
        """Convert messages to plain text for summarisation, stripping images."""
        parts: list[str] = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            if isinstance(content, str):
                parts.append(f"[{role}]: {content[:500]}")
            elif isinstance(content, list):
                text_blocks: list[str] = []
                for block in content:
                    btype = block.get("type")
                    if btype == "text":
                        text_blocks.append(block["text"][:300])
                    elif btype == "tool_use":
                        text_blocks.append(
                            f"[Tool: {block.get('name', '')}"
                            f"({json.dumps(block.get('input', {}))[:200]})]"
                        )
                    elif btype == "tool_result":
                        rc = block.get("content", "")
                        if isinstance(rc, str):
                            text_blocks.append(f"[Result: {rc[:200]}]")
                    elif btype == "image":
                        text_blocks.append("[Image: screenshot]")
                if text_blocks:
                    parts.append(f"[{role}]: " + " | ".join(text_blocks))

        text = "\n".join(parts)
        if len(text) > 10_000:
            text = text[:5000] + "\n...[truncated]...\n" + text[-5000:]
        return text

    def _summarize_tool_input(self, input_dict: dict) -> str:
        """Summarise tool input to key params."""
        if not input_dict:
            return ""
        parts: list[str] = []
        for k, v in input_dict.items():
            if k == "script":
                parts.append(f"script=<{len(str(v))} chars>")
            elif isinstance(v, str) and len(v) > 50:
                parts.append(f"{k}={v[:50]}...")
            else:
                parts.append(f"{k}={v}")
        return ", ".join(parts)

    def _summarize_tool_result(self, result: dict) -> str:
        """Summarise a tool result to key info."""
        if not result:
            return ""
        summary: dict = {}
        for k, v in result.items():
            if k == "image_base64":
                summary[k] = f"<{len(str(v))} chars base64>"
            elif isinstance(v, list) and len(v) > 5:
                summary[k] = f"<list of {len(v)} items>"
            else:
                summary[k] = v
        return json.dumps(summary, default=str)[:300]

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Return context management statistics."""
        return {
            "model": self.model,
            "context_window": self._context_window,
            "threshold_tokens": self._threshold,
            "condensation_count": self._condensation_count,
        }
