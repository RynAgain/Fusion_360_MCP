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

    def test_tools_count_is_27(self, client):
        data = client.get("/api/status").get_json()
        assert data["tools_count"] == 33


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
    def test_returns_list(self, client):
        data = client.get("/api/tools").get_json()
        assert isinstance(data, list)

    def test_has_27_tools(self, client):
        data = client.get("/api/tools").get_json()
        assert len(data) == 33

    def test_each_tool_has_category(self, client):
        data = client.get("/api/tools").get_json()
        for tool in data:
            assert "category" in tool
            assert isinstance(tool["category"], str)


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
