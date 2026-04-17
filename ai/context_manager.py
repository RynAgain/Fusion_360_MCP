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
import re
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
        total_tokens = 0  # TASK-024: Accumulate flat token costs (e.g. images)
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
                        # TASK-024: Anthropic charges ~1600 tokens per image
                        # regardless of base64 size. Using len(data)//3 was
                        # overestimating by ~36x, causing premature condensation.
                        total_tokens += 1600
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
                                    # TASK-024: Flat per-image cost
                                    total_tokens += 1600
        return total_tokens + total_chars // CHARS_PER_TOKEN

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

    # Regex to detect condensation summary markers (TASK-010)
    _CONDENSATION_HEADER_RE = re.compile(
        r"^\[Context Summary(?:\s*-\s*Condensation\s*#\d+)?\]",
        re.MULTILINE,
    )
    _CONDENSATION_END_RE = re.compile(
        r"\[End of summary\.[^\]]*\]",
    )

    @staticmethod
    def _strip_condensation_wrapper(text: str) -> str:
        """Extract substantive content from a condensation summary.

        Strips the ``[Context Summary - Condensation #N]`` header and the
        ``[End of summary ...]`` footer, returning only the design-state
        facts, user requests, and operation history within.  This prevents
        nested condensation boilerplate from accumulating across repeated
        condensation cycles (TASK-010).
        """
        if not text:
            return text
        # Remove header line:  [Context Summary - Condensation #N]
        text = re.sub(
            r"^\[Context Summary(?:\s*-\s*Condensation\s*#\d+)?\]\s*",
            "",
            text,
            count=1,
        )
        # Remove footer:  [End of summary. ...]
        text = re.sub(
            r"\[End of summary\.[^\]]*\]\s*$",
            "",
            text,
        )
        return text.strip()

    @staticmethod
    def _is_condensation_summary(text: str) -> bool:
        """Return True if *text* looks like a prior condensation summary."""
        if not text:
            return False
        return bool(re.match(
            r"^\[Context Summary",
            text.strip(),
        ))

    def condense(self, messages: list, client=None,
                 design_state_summary: str = None) -> list:
        """Condense conversation history by summarising older messages.

        Strategy:
        1. Split messages into *old* (to condense) and *recent* (to keep).
        2. Strip any prior condensation wrapper from old messages so nested
           summaries don't accumulate (TASK-010).
        3. If a Claude *client* is available, use it for an LLM summary.
        4. Otherwise fall back to rule-based extraction.
        5. If *design_state_summary* is provided, append it so the current
           design state survives condensation.
        6. Return ``[summary_message] + recent_messages``.

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

        # TASK-010: Before summarising, strip prior condensation wrappers
        # from old_messages so we don't nest summaries inside summaries.
        cleaned_old = self._strip_prior_condensations(old_messages)

        # Try LLM-based summarisation if a client is provided
        summary: Optional[str] = None
        if client:
            try:
                summary = self._llm_summarize(cleaned_old, client)
            except Exception as exc:
                logger.warning("LLM condensation failed: %s", exc)

        # Fallback to rule-based summarisation
        if not summary:
            summary = self._rule_based_summarize(cleaned_old)

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

    def _strip_prior_condensations(self, messages: list) -> list:
        """Return a copy of *messages* with prior condensation wrappers stripped.

        Any user message whose content is a condensation summary has its
        boilerplate removed so that only the substantive facts survive into
        the new summary.  This prevents the recursive nesting problem
        described in TASK-010.
        """
        cleaned: list = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user" and isinstance(content, str) and self._is_condensation_summary(content):
                stripped = self._strip_condensation_wrapper(content)
                if stripped:
                    # Re-wrap as a plain prior-state reference, not a "user request"
                    cleaned.append({
                        "role": "user",
                        "content": f"[Prior session state]\n{stripped}",
                    })
                # else: condensation was empty after stripping, drop it
            elif role == "user" and isinstance(content, list):
                # Handle list-form content blocks -- strip condensation text blocks
                new_blocks = []
                for block in content:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "text"
                        and self._is_condensation_summary(block.get("text", ""))
                    ):
                        stripped = self._strip_condensation_wrapper(block["text"])
                        if stripped:
                            new_blocks.append({
                                "type": "text",
                                "text": f"[Prior session state]\n{stripped}",
                            })
                    else:
                        new_blocks.append(block)
                if new_blocks:
                    cleaned.append({"role": "user", "content": new_blocks})
            else:
                cleaned.append(msg)
        return cleaned

    # ------------------------------------------------------------------
    # Truncation fallback
    # ------------------------------------------------------------------

    def _truncate(self, messages: list) -> list:
        """Simple truncation: remove oldest messages, keeping recent ones.

        TASK-023: Uses _find_safe_split_point to avoid splitting between
        a tool_use assistant message and its tool_result user message,
        which would cause Anthropic API rejection.
        """
        if len(messages) <= 4:
            return messages
        half = len(messages) // 2
        idx = self._find_safe_split_point(messages, half)
        return messages[idx:]

    # ------------------------------------------------------------------
    # LLM-based summarisation
    # ------------------------------------------------------------------

    def _llm_summarize(self, old_messages: list, client) -> Optional[str]:
        """Use an LLM to summarise old conversation (separate API call).

        TASK-011: Fixed to use the provider_manager abstraction instead of
        the non-existent ``client.client`` attribute.  Falls back to None
        so the caller uses rule-based summarisation.
        """
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

        # TASK-011: Use provider_manager.active.create_message() which is
        # the correct interface for all providers (Anthropic, Ollama, etc.)
        try:
            if (
                hasattr(client, "provider_manager")
                and client.provider_manager
                and client.provider_manager.active
                and client.provider_manager.active.is_available()
            ):
                model = getattr(client, "settings", None)
                model_id = model.model if model else "claude-sonnet-4-20250514"
                response = client.provider_manager.active.create_message(
                    messages=[
                        {
                            "role": "user",
                            "content": f"{summary_prompt}\n\n---\n\n{condensed_text}",
                        }
                    ],
                    system="You are a concise technical summarizer for CAD design sessions.",
                    tools=[],
                    max_tokens=1024,
                    model=model_id,
                )
                # response is an LLMResponse; extract text from content blocks
                if response and response.content:
                    for block in response.content:
                        if block.get("type") == "text" and block.get("text"):
                            return block["text"]
        except Exception as exc:
            logger.warning("LLM summarization failed (falling back to rules): %s", exc)

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
                    # TASK-010: Skip auto-screenshots and prior session state
                    # markers (already stripped condensation wrappers above)
                    if not content.startswith("[Auto-screenshot") and \
                       not content.startswith("[Context Summary") and \
                       not content.startswith("[Prior session state"):
                        user_requests.append(content[:200])
                elif isinstance(content, list):
                    for block in content:
                        if block.get("type") == "text":
                            text = block["text"]
                            if not text.startswith("[Auto-screenshot") and \
                               not text.startswith("[Context Summary") and \
                               not text.startswith("[Prior session state"):
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

    # ------------------------------------------------------------------
    # Output filtering (autoresearch redirect-and-grep pattern)
    # ------------------------------------------------------------------

    @staticmethod
    def filter_operation_output(
        output: str,
        max_chars: int = 2000,
        extract_patterns: list[str] | None = None,
    ) -> str:
        """Filter verbose operation output to keep the context window clean.

        Inspired by autoresearch's redirect-and-grep pattern -- only key
        information is kept in the conversation context.

        * If *output* is under *max_chars*, returns it as-is.
        * If it exceeds *max_chars*, keeps the first 500 chars (header),
          the last 500 chars (results/summary), and inserts a truncation
          marker in the middle.
        * If *extract_patterns* are provided (list of regex strings),
          matching lines from the **full** output are appended as a
          "Key Metrics" section.
        """
        if not output:
            return output

        extracted_lines: list[str] = []
        if extract_patterns:
            compiled = [re.compile(p) for p in extract_patterns]
            for line in output.splitlines():
                if any(pat.search(line) for pat in compiled):
                    extracted_lines.append(line)

        if len(output) <= max_chars and not extracted_lines:
            return output

        if len(output) <= max_chars:
            # Short output but we have extracted lines -- append them
            result = output
        else:
            # Truncate intelligently
            head_size = 500
            tail_size = 500
            truncated_count = len(output) - head_size - tail_size
            result = (
                output[:head_size]
                + f"\n[... truncated {truncated_count} chars ...]\n"
                + output[-tail_size:]
            )

        if extracted_lines:
            result += "\n\n--- Key Metrics ---\n" + "\n".join(extracted_lines)

        return result

    @staticmethod
    def summarize_fusion_response(response: dict) -> str:
        """Extract key fields from a verbose Fusion 360 API response.

        Returns a compact single-line or few-line summary containing
        success/failure, object IDs, dimensions, and errors.
        """
        if not response:
            return "<empty response>"

        parts: list[str] = []

        # Status
        status = response.get("status", "unknown")
        success = response.get("success")
        if success is not None:
            parts.append(f"status={status} success={success}")
        else:
            parts.append(f"status={status}")

        # Errors
        error = response.get("error") or response.get("message", "")
        if status == "error" and error:
            parts.append(f"error={error[:200]}")

        # Object IDs / names
        for key in ("body_name", "feature_name", "sketch_name", "sketch_id",
                     "component_name", "new_body_name", "line_id", "circle_id",
                     "arc_id", "parameter_name", "active_document",
                     "document_name", "file_path"):
            val = response.get(key)
            if val is not None:
                parts.append(f"{key}={val}")

        # Dimensions / measurements
        for key in ("volume_cm3", "surface_area_cm2", "distance_cm",
                     "area_cm2", "face_count", "edge_count", "vertex_count",
                     "count", "body_count", "component_count",
                     "file_size_bytes", "profile_count"):
            val = response.get(key)
            if val is not None:
                parts.append(f"{key}={val}")

        # Lists (bodies, documents) -- just count
        for key in ("bodies", "documents", "timeline", "features", "curves"):
            val = response.get(key)
            if isinstance(val, list):
                parts.append(f"{key}=[{len(val)} items]")

        return " | ".join(parts)

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
