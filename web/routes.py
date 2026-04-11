"""
web/routes.py
REST API blueprint for the Fusion 360 MCP web application.

All JSON endpoints live under /api/*; the root serves the SPA template.
"""

import logging
import platform

from flask import Blueprint, jsonify, render_template, request

from ai.conversation_manager import ConversationManager
from ai.system_prompt import get_prompt_stats

logger = logging.getLogger(__name__)

api = Blueprint("api", __name__)

# Conversation persistence layer
conversation_manager = ConversationManager()


# ---------------------------------------------------------------------------
# Helper — lazy import of shared components from web.app
# ---------------------------------------------------------------------------

def _components():
    """Return (bridge, mcp_server, claude_client) from the app module."""
    from web.app import bridge, mcp_server, claude_client
    return bridge, mcp_server, claude_client


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------

@api.route("/")
def index():
    """Serve the main SPA page."""
    return render_template("index.html")


# ---------------------------------------------------------------------------
# Status / health
# ---------------------------------------------------------------------------

@api.route("/api/status")
def status():
    """Return current system status."""
    from web.app import _detect_async_mode

    bridge, mcp_server, _cc = _components()
    return jsonify({
        "fusion_connected": bridge.is_connected() and not bridge.simulation_mode,
        "simulation_mode": bridge.simulation_mode,
        "tools_count": len(mcp_server.get_tool_names()),
        "platform": {
            "system": platform.system(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
            "async_mode": _detect_async_mode(),
        },
    })


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@api.route("/api/settings", methods=["GET"])
def get_settings():
    """Return current settings with the API key masked."""
    from config.settings import settings

    data = dict(settings._data)
    # Mask the API key for security
    raw_key = data.get("anthropic_api_key", "")
    if raw_key:
        data["anthropic_api_key"] = raw_key[:8] + "..." + raw_key[-4:] if len(raw_key) > 12 else "***"
    return jsonify(data)


@api.route("/api/settings", methods=["POST"])
def update_settings():
    """Update settings from a JSON body and return the new state."""
    from config.settings import settings
    from web.app import bridge, claude_client

    payload = request.get_json(silent=True) or {}
    logger.info("Settings update request: %s", {k: ("***" if "key" in k.lower() else v) for k, v in payload.items()})

    settings.update(payload)

    # Propagate simulation_mode change to bridge
    if "fusion_simulation_mode" in payload:
        bridge._forced_sim = bool(payload["fusion_simulation_mode"])
        bridge.simulation_mode = bridge._forced_sim

    # Return the refreshed settings (masked)
    return get_settings()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@api.route("/api/tools")
def list_tools():
    """Return tool definitions with categories."""
    from mcp.server import TOOL_DEFINITIONS, TOOL_CATEGORIES

    tools = []
    for tool in TOOL_DEFINITIONS:
        tools.append({
            **tool,
            "category": TOOL_CATEGORIES.get(tool["name"], "General"),
        })
    return jsonify(tools)


# ---------------------------------------------------------------------------
# Fusion bridge connection
# ---------------------------------------------------------------------------

@api.route("/api/connect", methods=["POST"])
def connect_fusion():
    """Connect to the Fusion 360 add-in."""
    bridge, _ms, _cc = _components()
    result = bridge.connect()
    logger.info("Fusion connect result: %s", result)
    return jsonify(result)


@api.route("/api/disconnect", methods=["POST"])
def disconnect_fusion():
    """Disconnect from the Fusion 360 add-in."""
    bridge, _ms, _cc = _components()
    bridge.disconnect()
    return jsonify({"status": "ok", "message": "Disconnected from Fusion 360."})


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

@api.route("/api/conversations", methods=["GET"])
def list_conversations():
    """List all saved conversations (metadata only)."""
    return jsonify(conversation_manager.list_all())


@api.route("/api/conversations", methods=["POST"])
def save_conversation():
    """Save the current conversation to disk."""
    _bridge, _ms, cc = _components()
    payload = request.get_json(silent=True) or {}
    title = payload.get("title")  # optional override
    meta = conversation_manager.save(
        conversation_id=cc.get_conversation_id(),
        messages=cc.get_messages(),
        title=title,
    )
    return jsonify(meta), 201


@api.route("/api/conversations/<conversation_id>", methods=["GET"])
def get_conversation(conversation_id):
    """Load a single conversation (including messages)."""
    data = conversation_manager.load(conversation_id)
    if data is None:
        return jsonify({"error": "Conversation not found"}), 404
    return jsonify(data)


@api.route("/api/conversations/<conversation_id>", methods=["DELETE"])
def delete_conversation(conversation_id):
    """Delete a saved conversation."""
    deleted = conversation_manager.delete(conversation_id)
    if not deleted:
        return jsonify({"error": "Conversation not found"}), 404
    return jsonify({"status": "ok", "message": f"Conversation {conversation_id} deleted."})


@api.route("/api/conversations/<conversation_id>/load", methods=["POST"])
def load_conversation_into_client(conversation_id):
    """
    Load a saved conversation into the active Claude client,
    replacing the current in-memory history and conversation ID.
    """
    data = conversation_manager.load(conversation_id)
    if data is None:
        return jsonify({"error": "Conversation not found"}), 404

    _bridge, _ms, cc = _components()
    cc.set_conversation(conversation_id, data.get("messages", []))
    return jsonify({
        "status": "ok",
        "message": f"Loaded conversation {conversation_id} ({data.get('message_count', 0)} messages).",
        "conversation_id": conversation_id,
    })


# ---------------------------------------------------------------------------
# Prompt stats
# ---------------------------------------------------------------------------

@api.route("/api/prompt-stats", methods=["GET"])
def prompt_stats():
    """Return statistics about the current system prompt."""
    return jsonify(get_prompt_stats())
