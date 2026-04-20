"""
tests/test_auto_approval.py
Unit tests for ai.auto_approval -- AutoApprovalHandler limits and state.
"""
import pytest

from ai.auto_approval import AutoApprovalHandler, AutoApprovalResult


# ---------------------------------------------------------------------------
# AutoApprovalResult
# ---------------------------------------------------------------------------

class TestAutoApprovalResult:

    def test_default_result(self):
        r = AutoApprovalResult(should_proceed=True)
        assert r.should_proceed is True
        assert r.requires_approval is False
        assert r.approval_type is None
        assert r.count == 0

    def test_approval_required_result(self):
        r = AutoApprovalResult(
            should_proceed=False, requires_approval=True,
            approval_type="requests", count=25,
        )
        assert r.should_proceed is False
        assert r.requires_approval is True
        assert r.approval_type == "requests"
        assert r.count == 25


# ---------------------------------------------------------------------------
# Default state
# ---------------------------------------------------------------------------

class TestDefaultState:

    def test_initial_request_count_is_zero(self):
        h = AutoApprovalHandler()
        assert h.request_count == 0

    def test_initial_cumulative_cost_is_zero(self):
        h = AutoApprovalHandler()
        assert h.cumulative_cost == 0.0

    def test_default_limits(self):
        h = AutoApprovalHandler()
        d = h.to_dict()
        assert d["max_requests"] == 25
        assert d["max_cost"] == 1.0

    def test_custom_limits_in_constructor(self):
        h = AutoApprovalHandler(max_auto_requests=10, max_auto_cost=0.5)
        d = h.to_dict()
        assert d["max_requests"] == 10
        assert d["max_cost"] == 0.5


# ---------------------------------------------------------------------------
# record_request
# ---------------------------------------------------------------------------

class TestRecordRequest:

    def test_increments_request_count(self):
        h = AutoApprovalHandler()
        h.record_request()
        assert h.request_count == 1
        h.record_request()
        assert h.request_count == 2

    def test_accumulates_cost(self):
        h = AutoApprovalHandler()
        h.record_request(cost=0.01)
        h.record_request(cost=0.02)
        assert h.cumulative_cost == pytest.approx(0.03)

    def test_zero_cost_default(self):
        h = AutoApprovalHandler()
        h.record_request()
        assert h.cumulative_cost == 0.0


# ---------------------------------------------------------------------------
# check_limits -- request limit
# ---------------------------------------------------------------------------

class TestRequestLimit:

    def test_under_limit_proceeds(self):
        h = AutoApprovalHandler(max_auto_requests=5)
        for _ in range(4):
            h.record_request()
        result = h.check_limits()
        assert result.should_proceed is True
        assert result.requires_approval is False

    def test_at_limit_requires_approval(self):
        h = AutoApprovalHandler(max_auto_requests=5)
        for _ in range(5):
            h.record_request()
        result = h.check_limits()
        assert result.should_proceed is False
        assert result.requires_approval is True
        assert result.approval_type == "requests"
        assert result.count == 5

    def test_over_limit_requires_approval(self):
        h = AutoApprovalHandler(max_auto_requests=3)
        for _ in range(10):
            h.record_request()
        result = h.check_limits()
        assert result.should_proceed is False
        assert result.approval_type == "requests"


# ---------------------------------------------------------------------------
# check_limits -- cost limit
# ---------------------------------------------------------------------------

class TestCostLimit:

    def test_under_cost_limit_proceeds(self):
        h = AutoApprovalHandler(max_auto_cost=1.0)
        h.record_request(cost=0.5)
        result = h.check_limits()
        assert result.should_proceed is True

    def test_at_cost_limit_requires_approval(self):
        h = AutoApprovalHandler(max_auto_cost=1.0)
        h.record_request(cost=1.0)
        result = h.check_limits()
        assert result.should_proceed is False
        assert result.requires_approval is True
        assert result.approval_type == "cost"
        assert result.count == pytest.approx(1.0)

    def test_over_cost_limit_requires_approval(self):
        h = AutoApprovalHandler(max_auto_cost=0.50)
        h.record_request(cost=0.30)
        h.record_request(cost=0.30)
        result = h.check_limits()
        assert result.should_proceed is False
        assert result.approval_type == "cost"

    def test_cost_checked_before_requests_when_both_exceeded(self):
        """When both limits are exceeded, request limit is checked first."""
        h = AutoApprovalHandler(max_auto_requests=2, max_auto_cost=0.10)
        h.record_request(cost=0.05)
        h.record_request(cost=0.06)
        result = h.check_limits()
        # Request limit is checked first in the code
        assert result.should_proceed is False
        assert result.approval_type == "requests"


# ---------------------------------------------------------------------------
# Unlimited mode (0 = no limit)
# ---------------------------------------------------------------------------

class TestUnlimitedMode:

    def test_unlimited_requests(self):
        h = AutoApprovalHandler(max_auto_requests=0, max_auto_cost=0.0)
        for _ in range(1000):
            h.record_request(cost=0.01)
        result = h.check_limits()
        assert result.should_proceed is True
        assert result.requires_approval is False

    def test_unlimited_requests_but_cost_limited(self):
        h = AutoApprovalHandler(max_auto_requests=0, max_auto_cost=0.50)
        for _ in range(100):
            h.record_request(cost=0.01)
        result = h.check_limits()
        assert result.should_proceed is False
        assert result.approval_type == "cost"

    def test_unlimited_cost_but_request_limited(self):
        h = AutoApprovalHandler(max_auto_requests=5, max_auto_cost=0.0)
        for _ in range(5):
            h.record_request(cost=100.0)
        result = h.check_limits()
        assert result.should_proceed is False
        assert result.approval_type == "requests"


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------

class TestReset:

    def test_reset_clears_request_count(self):
        h = AutoApprovalHandler()
        for _ in range(10):
            h.record_request()
        h.reset()
        assert h.request_count == 0

    def test_reset_clears_cumulative_cost(self):
        h = AutoApprovalHandler()
        h.record_request(cost=5.0)
        h.reset()
        assert h.cumulative_cost == 0.0

    def test_reset_allows_continued_operation(self):
        h = AutoApprovalHandler(max_auto_requests=3)
        for _ in range(3):
            h.record_request()
        assert h.check_limits().should_proceed is False
        h.reset()
        assert h.check_limits().should_proceed is True


# ---------------------------------------------------------------------------
# configure
# ---------------------------------------------------------------------------

class TestConfigure:

    def test_configure_updates_max_requests(self):
        h = AutoApprovalHandler(max_auto_requests=10)
        h.configure(max_auto_requests=50)
        assert h.to_dict()["max_requests"] == 50

    def test_configure_updates_max_cost(self):
        h = AutoApprovalHandler(max_auto_cost=1.0)
        h.configure(max_auto_cost=5.0)
        assert h.to_dict()["max_cost"] == 5.0

    def test_configure_none_leaves_unchanged(self):
        h = AutoApprovalHandler(max_auto_requests=10, max_auto_cost=2.0)
        h.configure(max_auto_requests=None, max_auto_cost=None)
        assert h.to_dict()["max_requests"] == 10
        assert h.to_dict()["max_cost"] == 2.0

    def test_configure_partial_update(self):
        h = AutoApprovalHandler(max_auto_requests=10, max_auto_cost=2.0)
        h.configure(max_auto_requests=20)
        assert h.to_dict()["max_requests"] == 20
        assert h.to_dict()["max_cost"] == 2.0


# ---------------------------------------------------------------------------
# to_dict
# ---------------------------------------------------------------------------

class TestToDict:

    def test_all_keys_present(self):
        h = AutoApprovalHandler()
        d = h.to_dict()
        expected_keys = {
            "request_count", "cumulative_cost", "max_requests",
            "max_cost", "requests_remaining", "cost_remaining",
        }
        assert set(d.keys()) == expected_keys

    def test_remaining_calculated_correctly(self):
        h = AutoApprovalHandler(max_auto_requests=10, max_auto_cost=1.0)
        h.record_request(cost=0.3)
        h.record_request(cost=0.2)
        d = h.to_dict()
        assert d["request_count"] == 2
        assert d["cumulative_cost"] == pytest.approx(0.5)
        assert d["requests_remaining"] == 8
        assert d["cost_remaining"] == pytest.approx(0.5)

    def test_remaining_never_negative(self):
        h = AutoApprovalHandler(max_auto_requests=2, max_auto_cost=0.10)
        for _ in range(5):
            h.record_request(cost=0.05)
        d = h.to_dict()
        assert d["requests_remaining"] == 0
        assert d["cost_remaining"] == 0.0

    def test_unlimited_remaining_is_negative_one(self):
        h = AutoApprovalHandler(max_auto_requests=0, max_auto_cost=0.0)
        d = h.to_dict()
        assert d["requests_remaining"] == -1
        assert d["cost_remaining"] == -1

    def test_cumulative_cost_rounded(self):
        h = AutoApprovalHandler()
        h.record_request(cost=0.00001)
        h.record_request(cost=0.00002)
        d = h.to_dict()
        assert d["cumulative_cost"] == 0.0
        # Rounded to 4 decimal places
        h.record_request(cost=0.12345)
        d = h.to_dict()
        assert d["cumulative_cost"] == 0.1235


# ---------------------------------------------------------------------------
# TASK-202: Floating-point cost precision
# ---------------------------------------------------------------------------

class TestDecimalCostPrecision:
    """TASK-202: Cost tracking must use Decimal internally to avoid float drift."""

    def test_many_small_floats_exact_limit(self):
        """Adding 0.001 one hundred times should equal exactly 0.1."""
        h = AutoApprovalHandler(max_auto_requests=0, max_auto_cost=0.1)
        for _ in range(100):
            h.record_request(cost=0.001)
        # With Decimal, cumulative cost is exactly 0.1, so >= limit
        result = h.check_limits()
        assert result.should_proceed is False
        assert result.approval_type == "cost"

    def test_many_small_floats_just_under_limit(self):
        """Adding 0.001 ninety-nine times should be under 0.1."""
        h = AutoApprovalHandler(max_auto_requests=0, max_auto_cost=0.1)
        for _ in range(99):
            h.record_request(cost=0.001)
        result = h.check_limits()
        assert result.should_proceed is True


# ---------------------------------------------------------------------------
# TASK-204: _last_reset_index removed
# ---------------------------------------------------------------------------

class TestLastResetIndexRemoved:
    """TASK-204: _last_reset_index is dead code and must be removed."""

    def test_no_last_reset_index_attribute(self):
        h = AutoApprovalHandler()
        assert not hasattr(h, '_last_reset_index')

    def test_no_last_reset_index_after_reset(self):
        h = AutoApprovalHandler()
        h.record_request(cost=0.5)
        h.reset()
        assert not hasattr(h, '_last_reset_index')
