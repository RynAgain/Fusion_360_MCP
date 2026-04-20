"""
ai/auto_approval.py
Auto-approval handler with configurable request and cost limits.

Tracks consecutive auto-approved tool calls within a turn and cumulative
API cost. When limits are reached, returns a result indicating the user
should be prompted for approval to continue.
"""
import logging
from decimal import Decimal

logger = logging.getLogger(__name__)


class AutoApprovalResult:
    """Result of an auto-approval check."""
    def __init__(self, should_proceed: bool, requires_approval: bool = False,
                 approval_type: str | None = None, count: int | float = 0):
        self.should_proceed = should_proceed
        self.requires_approval = requires_approval
        self.approval_type = approval_type  # "requests" or "cost"
        self.count = count


class AutoApprovalHandler:
    """Tracks auto-approved operations and enforces configurable limits.

    When auto-approval is enabled, tool calls proceed without user confirmation
    up to the configured limits. Once a limit is reached, the handler signals
    that user approval is required before continuing.

    Limits:
    - max_auto_requests: Maximum consecutive auto-approved tool calls (0 = unlimited)
    - max_auto_cost: Maximum cumulative API cost in dollars (0.0 = unlimited)
    """

    def __init__(self, max_auto_requests: int = 25, max_auto_cost: float = 1.0):
        self._max_requests = max_auto_requests
        self._max_cost = Decimal(str(max_auto_cost))
        self._request_count = 0
        self._cumulative_cost = Decimal("0")

    @property
    def request_count(self) -> int:
        return self._request_count

    @property
    def cumulative_cost(self) -> float:
        return float(self._cumulative_cost)

    def configure(self, max_auto_requests: int | None = None,
                  max_auto_cost: float | None = None) -> None:
        """Update limits."""
        if max_auto_requests is not None:
            self._max_requests = max_auto_requests
        if max_auto_cost is not None:
            self._max_cost = Decimal(str(max_auto_cost))

    def record_request(self, cost: float = 0.0) -> None:
        """Record a completed auto-approved request."""
        self._request_count += 1
        self._cumulative_cost += Decimal(str(cost))

    def check_limits(self) -> AutoApprovalResult:
        """Check if auto-approval limits have been reached.

        Returns:
            AutoApprovalResult indicating whether to proceed or pause.
        """
        # Check request count limit
        if self._max_requests > 0 and self._request_count >= self._max_requests:
            logger.info(
                "Auto-approval request limit reached: %d/%d",
                self._request_count, self._max_requests,
            )
            return AutoApprovalResult(
                should_proceed=False,
                requires_approval=True,
                approval_type="requests",
                count=self._request_count,
            )

        # Check cost limit
        if self._max_cost > 0 and self._cumulative_cost >= self._max_cost:
            logger.info(
                "Auto-approval cost limit reached: $%.4f/$%.2f",
                float(self._cumulative_cost), float(self._max_cost),
            )
            return AutoApprovalResult(
                should_proceed=False,
                requires_approval=True,
                approval_type="cost",
                count=float(self._cumulative_cost),
            )

        return AutoApprovalResult(should_proceed=True)

    def reset(self) -> None:
        """Reset counters (e.g., after user approves continuation)."""
        self._request_count = 0
        self._cumulative_cost = Decimal("0")
        logger.debug("Auto-approval counters reset")

    def to_dict(self) -> dict:
        """Return current state for UI consumption."""
        cost_float = float(self._cumulative_cost)
        max_cost_float = float(self._max_cost)
        return {
            "request_count": self._request_count,
            "cumulative_cost": round(cost_float, 4),
            "max_requests": self._max_requests,
            "max_cost": max_cost_float,
            "requests_remaining": max(0, self._max_requests - self._request_count) if self._max_requests > 0 else -1,
            "cost_remaining": round(max(0.0, max_cost_float - cost_float), 4) if max_cost_float > 0 else -1,
        }
