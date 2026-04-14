"""
tests/test_web_routes.py
Unit tests for the Flask REST API endpoints defined in web/routes.py.

Uses the Flask test client to exercise JSON endpoints without
needing a running server or Socket.IO / eventlet.
"""

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    """
    Create a Flask test client via the application factory.

    The factory sets up shared components (bridge, mcp_server, claude_client)
    in simulation mode, so all endpoints work without Fusion 360 or an API key.
    """
    from web.app import create_app

    app, _socketio = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------

class TestIndexRoute:
    def test_returns_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_returns_html(self, client):
        resp = client.get("/")
        assert "text/html" in resp.content_type


# ---------------------------------------------------------------------------
# /api/status
# ---------------------------------------------------------------------------

class TestStatusEndpoint:
    def test_returns_json(self, client):
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, dict)

    def test_has_expected_keys(self, client):
        data = client.get("/api/status").get_json()
        assert "fusion_connected" in data
        assert "simulation_mode" in data
        assert "tools_count" in data

    def test_simulation_mode_true(self, client):
        """Default factory creates bridge in simulation mode."""
        data = client.get("/api/status").get_json()
        assert data["simulation_mode"] is True

    def test_tools_count_is_38(self, client):
        data = client.get("/api/status").get_json()
        assert data["tools_count"] == 38


# ---------------------------------------------------------------------------
# /api/settings
# ---------------------------------------------------------------------------

class TestSettingsEndpoint:
    def test_get_returns_json(self, client):
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        assert isinstance(resp.get_json(), dict)

    def test_api_key_is_masked(self, client):
        data = client.get("/api/settings").get_json()
        raw = data.get("anthropic_api_key", "")
        # If key is set it should be masked; if empty that's also fine
        if raw:
            assert "..." in raw or raw == "***"

    def test_post_updates_settings(self, client):
        resp = client.post(
            "/api/settings",
            json={"theme": "light"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("theme") == "light"


# ---------------------------------------------------------------------------
# /api/tools
# ---------------------------------------------------------------------------

class TestToolsEndpoint:
    def test_returns_dict_with_tools(self, client):
        data = client.get("/api/tools").get_json()
        assert isinstance(data, dict)
        assert "tools" in data
        assert isinstance(data["tools"], list)

    def test_has_38_tools(self, client):
        data = client.get("/api/tools").get_json()
        assert len(data["tools"]) == 38
        assert data["total"] == 38
        assert data["filtered"] == 38

    def test_each_tool_has_category(self, client):
        data = client.get("/api/tools").get_json()
        for tool in data["tools"]:
            assert "category" in tool
            assert isinstance(tool["category"], str)

    def test_includes_mode_info(self, client):
        data = client.get("/api/tools").get_json()
        assert "mode" in data
        assert data["mode"] == "full"


# ---------------------------------------------------------------------------
# /api/connect
# ---------------------------------------------------------------------------

class TestConnectEndpoint:
    def test_connect_returns_json(self, client):
        resp = client.post("/api/connect")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "status" in data
        # In simulation mode the status will be "simulation"
        assert data["status"] in ("simulation", "success", "error")


# ---------------------------------------------------------------------------
# /api/conversations
# ---------------------------------------------------------------------------

class TestConversationsEndpoint:
    def test_get_returns_list(self, client):
        resp = client.get("/api/conversations")
        assert resp.status_code == 200
        assert isinstance(resp.get_json(), list)


# ---------------------------------------------------------------------------
# /api/timeline
# ---------------------------------------------------------------------------

class TestTimelineEndpoint:
    def test_returns_json(self, client):
        resp = client.get("/api/timeline")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, dict)

    def test_has_timeline_key(self, client):
        data = client.get("/api/timeline").get_json()
        assert "timeline" in data
        assert isinstance(data["timeline"], list)

    def test_simulation_returns_items(self, client):
        """In simulation mode the bridge returns a simulated timeline."""
        data = client.get("/api/timeline").get_json()
        assert data.get("success") is True
        assert len(data["timeline"]) > 0


# ---------------------------------------------------------------------------
# /api/prompt-stats
# ---------------------------------------------------------------------------

class TestPromptStatsEndpoint:
    def test_returns_json(self, client):
        resp = client.get("/api/prompt-stats")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, dict)

    def test_has_expected_keys(self, client):
        data = client.get("/api/prompt-stats").get_json()
        for key in ("total_chars", "estimated_tokens", "skill_doc_loaded", "skill_doc_chars"):
            assert key in data


# ---------------------------------------------------------------------------
# /api/orchestration
# ---------------------------------------------------------------------------

class TestOrchestrationEndpoints:
    """Tests for the orchestration REST endpoints."""

    def test_create_orchestrated_plan(self, client):
        """POST plan with valid title and steps returns 200."""
        resp = client.post(
            "/api/orchestration/plan",
            json={
                "title": "Test Plan",
                "steps": [
                    {"description": "Step one", "mode_hint": "sketch"},
                    {"description": "Step two", "mode_hint": "modeling", "depends_on": [0]},
                ],
            },
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "plan_summary" in data
        assert data["plan_summary"]["title"] == "Test Plan"

    def test_create_orchestrated_plan_missing_fields(self, client):
        """POST plan without title or steps returns 400."""
        # Missing steps
        resp = client.post(
            "/api/orchestration/plan",
            json={"title": "No Steps"},
            content_type="application/json",
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["status"] == "error"

        # Missing title
        resp = client.post(
            "/api/orchestration/plan",
            json={"steps": [{"description": "A step"}]},
            content_type="application/json",
        )
        assert resp.status_code == 400

        # Empty body
        resp = client.post(
            "/api/orchestration/plan",
            json={},
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_get_orchestration_status(self, client):
        """GET status returns a dict with expected keys."""
        resp = client.get("/api/orchestration/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, dict)
        assert "has_plan" in data
        assert "is_executing" in data
        assert "execution_summary" in data

    def test_execute_next_no_plan(self, client):
        """POST execute/next with no plan returns 400."""
        resp = client.post(
            "/api/orchestration/execute/next",
            json={},
            content_type="application/json",
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["status"] == "error"
        assert "No orchestrated plan" in data["message"]

    def test_delete_plan(self, client):
        """DELETE clears an existing plan."""
        # Create a plan first
        client.post(
            "/api/orchestration/plan",
            json={
                "title": "To Delete",
                "steps": [{"description": "Step"}],
            },
            content_type="application/json",
        )
        # Now delete
        resp = client.delete("/api/orchestration/plan")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"

        # Verify status shows no plan
        status = client.get("/api/orchestration/status").get_json()
        assert status["has_plan"] is False

    def test_create_plan_and_get_status(self, client):
        """Integration: create a plan then verify status reflects it."""
        # Create plan
        client.post(
            "/api/orchestration/plan",
            json={
                "title": "Integration Plan",
                "steps": [
                    {"description": "First step"},
                    {"description": "Second step", "depends_on": [0]},
                    {"description": "Third step", "depends_on": [1]},
                ],
            },
            content_type="application/json",
        )
        # Check status
        resp = client.get("/api/orchestration/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["has_plan"] is True
        assert data["plan_summary"]["title"] == "Integration Plan"
        assert data["plan_summary"]["total_steps"] == 3
