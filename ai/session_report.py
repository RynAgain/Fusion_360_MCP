"""
ai/session_report.py
TASK-238: Post-session failure analysis report.

When a session ends abnormally (iteration limit, repeated failures, empty
responses, user cancel with significant issues), generates a structured
report summarising what went wrong.

The report is:
- Generated as a dict/JSON structure
- Saved alongside the conversation JSON in ``data/conversations/``
  with suffix ``_failure_report.json``
- Logged at WARNING level
- Emitted as an event to the UI

Usage::

    from ai.session_report import SessionFailureReport

    report = SessionFailureReport()
    report.set_termination_reason("iteration_limit")
    report.collect(
        progress_tracker=progress_tracker,
        script_error_tracker=script_error_tracker,
        rebuild_loop_detector=rebuild_loop_detector,
        mcp_server=mcp_server,
        context_pressure_triggered=True,
    )

    if report.should_generate():
        data = report.to_dict()
        report.save(conversation_id)
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Directory for saved conversations (same as ConversationManager)
_CONVERSATIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "conversations"
)


class SessionFailureReport:
    """Generates a structured report when a session ends abnormally.

    TASK-238: Collects data from multiple tracker subsystems and produces
    a JSON-serialisable report dict.
    """

    def __init__(self) -> None:
        self._termination_reason: str = "unknown"
        self._error_summary: dict[str, Any] = {}
        self._tool_usage_stats: dict[str, Any] = {}
        self._rebuild_count: int = 0
        self._blocklisted_tools: list[str] = []
        self._context_pressure_triggered: bool = False
        self._collected: bool = False

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def set_termination_reason(self, reason: str) -> None:
        """Set the reason the session terminated.

        Valid reasons: ``"iteration_limit"``, ``"empty_responses"``,
        ``"user_cancel"``, ``"force_stop"``, ``"error"``, ``"normal"``.
        """
        self._termination_reason = reason

    # ------------------------------------------------------------------
    # Data collection
    # ------------------------------------------------------------------

    def collect(
        self,
        progress_tracker: Any = None,
        script_error_tracker: Any = None,
        rebuild_loop_detector: Any = None,
        mcp_server: Any = None,
        context_pressure_triggered: bool = False,
    ) -> None:
        """Collect data from available tracker subsystems.

        All parameters are optional -- the report gracefully handles
        missing subsystems by recording empty/default values.

        Args:
            progress_tracker: :class:`ai.progress_tracker.ProgressTracker`
            script_error_tracker: :class:`ai.repetition_detector.ScriptErrorTracker`
            rebuild_loop_detector: :class:`ai.repetition_detector.RebuildLoopDetector`
            mcp_server: :class:`mcp.server.MCPServer` (for blocklisted tools)
            context_pressure_triggered: Whether context pressure was triggered.
        """
        self._context_pressure_triggered = context_pressure_triggered

        # Error summary from ScriptErrorTracker
        if script_error_tracker is not None:
            try:
                stats = script_error_tracker.get_stats()
                self._error_summary = {
                    "unique_errors": stats.get("unique_signatures", 0),
                    "total_script_errors": stats.get("total_errors", 0),
                    "top_errors": stats.get("top_errors", []),
                    "blocked_patterns": stats.get("blocked_count", 0),
                }
            except Exception as exc:
                logger.debug("TASK-238: Failed to collect script error stats: %s", exc)
                self._error_summary = {"collection_error": str(exc)}
        else:
            self._error_summary = {}

        # Tool usage stats from ProgressTracker
        if progress_tracker is not None:
            try:
                self._tool_usage_stats = progress_tracker.to_dict()
            except Exception as exc:
                logger.debug("TASK-238: Failed to collect progress stats: %s", exc)
                self._tool_usage_stats = {"collection_error": str(exc)}
        else:
            self._tool_usage_stats = {}

        # Rebuild count from RebuildLoopDetector
        if rebuild_loop_detector is not None:
            try:
                self._rebuild_count = getattr(rebuild_loop_detector, "count", 0)
            except Exception as exc:
                logger.debug("TASK-238: Failed to collect rebuild count: %s", exc)
                self._rebuild_count = 0
        else:
            self._rebuild_count = 0

        # Blocklisted tools from MCPServer
        if mcp_server is not None:
            try:
                bl = getattr(mcp_server, "blocklisted_tools", set())
                self._blocklisted_tools = sorted(bl)
            except Exception as exc:
                logger.debug("TASK-238: Failed to collect blocklisted tools: %s", exc)
                self._blocklisted_tools = []
        else:
            self._blocklisted_tools = []

        self._collected = True

    # ------------------------------------------------------------------
    # Decision logic
    # ------------------------------------------------------------------

    def should_generate(self) -> bool:
        """Return True if this session had significant failures.

        "Significant failures" means any of:
        - Iteration limit was hit
        - Rebuild count > 1
        - Thrashing ratio > 0.5
        - Blocklisted tools > 0
        - Termination due to empty responses or force_stop
        """
        if self._termination_reason in (
            "iteration_limit",
            "empty_responses",
            "force_stop",
            "error",
        ):
            return True

        if self._rebuild_count > 1:
            return True

        thrashing_ratio = self._tool_usage_stats.get("thrashing_ratio", 0.0)
        if isinstance(thrashing_ratio, (int, float)) and thrashing_ratio > 0.5:
            return True

        if self._blocklisted_tools:
            return True

        return False

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return the report as a JSON-serialisable dict."""
        return {
            "report_type": "session_failure_report",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "termination_reason": self._termination_reason,
            "error_summary": self._error_summary,
            "tool_usage_stats": self._tool_usage_stats,
            "rebuild_count": self._rebuild_count,
            "blocklisted_tools": self._blocklisted_tools,
            "context_pressure_triggered": self._context_pressure_triggered,
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, conversation_id: str) -> Optional[str]:
        """Save the report alongside the conversation JSON.

        Args:
            conversation_id: UUID of the conversation.

        Returns:
            The file path of the saved report, or None on failure.
        """
        report_data = self.to_dict()
        report_data["conversation_id"] = conversation_id

        # Log the report at WARNING level
        logger.warning(
            "TASK-238: Session failure report for conversation %s: "
            "reason=%s, errors=%s, rebuilds=%d, blocklisted=%d, "
            "thrashing_ratio=%.2f",
            conversation_id,
            self._termination_reason,
            self._error_summary.get("unique_errors", 0),
            self._rebuild_count,
            len(self._blocklisted_tools),
            self._tool_usage_stats.get("thrashing_ratio", 0.0),
        )

        # Save to disk
        try:
            os.makedirs(_CONVERSATIONS_DIR, exist_ok=True)
            filename = f"{conversation_id}_failure_report.json"
            filepath = os.path.join(_CONVERSATIONS_DIR, filename)
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(report_data, f, indent=2, default=str)
            logger.info("TASK-238: Saved failure report to %s", filepath)
            return filepath
        except Exception as exc:
            logger.error("TASK-238: Failed to save failure report: %s", exc)
            return None
