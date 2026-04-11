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
# Helper -- lazy import of shared components from web.app
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

    # Resolve the *real* key (env var > encoded config > plain config)
    # then mask it for the browser.
    real_key = settings.api_key
    if real_key:
        data["anthropic_api_key"] = (
            real_key[:8] + "..." + real_key[-4:]
            if len(real_key) > 12
            else "***"
        )
    else:
        data["anthropic_api_key"] = ""

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

    # Propagate provider-related changes to ClaudeClient
    if claude_client:
        if "provider" in payload:
            try:
                claude_client.provider_manager.switch(payload["provider"])
            except ValueError:
                pass
        if "anthropic_api_key" in payload:
            real_key = settings.api_key  # resolved after obfuscation
            claude_client.provider_manager.configure_provider(
                "anthropic", api_key=real_key
            )
        if "ollama_base_url" in payload:
            claude_client.provider_manager.configure_provider(
                "ollama", base_url=payload["ollama_base_url"]
            )

    # Return the refreshed settings (masked)
    return get_settings()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@api.route("/api/tools")
def list_tools():
    """Return tool definitions with categories, filtered by active mode."""
    from mcp.server import TOOL_DEFINITIONS, TOOL_CATEGORIES

    _bridge, _ms, cc = _components()

    # Determine which tools are allowed in the current mode
    if cc:
        allowed = cc.mode_manager.get_allowed_tools()
    else:
        allowed = None  # No filtering if client unavailable

    all_tools = []
    filtered = []
    for tool in TOOL_DEFINITIONS:
        entry = {
            **tool,
            "category": TOOL_CATEGORIES.get(tool["name"], "General"),
        }
        all_tools.append(entry)
        if allowed is None or tool["name"] in allowed:
            filtered.append(entry)

    return jsonify({
        "tools": filtered,
        "total": len(all_tools),
        "filtered": len(filtered),
        "mode": cc.mode_manager.active_slug if cc else "full",
    })


# ---------------------------------------------------------------------------
# Design Timeline (Feature 2)
# ---------------------------------------------------------------------------

@api.route("/api/timeline")
def get_timeline():
    """Return the Fusion 360 design timeline via the bridge."""
    bridge, _ms, _cc = _components()
    try:
        result = bridge.get_timeline()
        return jsonify(result)
    except Exception as exc:
        logger.error("Timeline fetch failed: %s", exc)
        return jsonify({"success": False, "error": str(exc), "timeline": []})


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
# Document management
# ---------------------------------------------------------------------------

@api.route("/api/documents", methods=["GET"])
def list_documents():
    """List all open Fusion 360 documents."""
    bridge, _ms, _cc = _components()
    result = bridge.execute("list_documents", {})
    return jsonify(result)


@api.route("/api/documents/switch", methods=["POST"])
def switch_document():
    """Switch the active document."""
    bridge, _ms, _cc = _components()
    data = request.get_json(silent=True) or {}
    result = bridge.execute("switch_document", data)
    return jsonify(result)


@api.route("/api/documents/new", methods=["POST"])
def new_document():
    """Create a new Fusion 360 design document."""
    bridge, _ms, _cc = _components()
    data = request.get_json(silent=True) or {}
    result = bridge.execute("new_document", data)
    return jsonify(result)


@api.route("/api/documents/close", methods=["POST"])
def close_document():
    """Close an open document."""
    bridge, _ms, _cc = _components()
    data = request.get_json(silent=True) or {}
    result = bridge.execute("close_document", data)
    return jsonify(result)


# ---------------------------------------------------------------------------
# Prompt stats
# ---------------------------------------------------------------------------

@api.route("/api/prompt-stats", methods=["GET"])
def prompt_stats():
    """Return statistics about the current system prompt."""
    return jsonify(get_prompt_stats())


# ---------------------------------------------------------------------------
# Mode management
# ---------------------------------------------------------------------------

@api.route("/api/modes", methods=["GET"])
def list_modes():
    """List available CAD modes."""
    _bridge, _ms, cc = _components()
    if cc:
        return jsonify({
            "modes": cc.mode_manager.list_modes(),
            "active": cc.mode_manager.active_slug,
        })
    return jsonify({"modes": [], "active": "full"})


@api.route("/api/modes/<slug>", methods=["POST"])
def switch_mode(slug):
    """Switch to a different CAD mode."""
    _bridge, _ms, cc = _components()
    if cc:
        try:
            mode = cc.switch_mode(slug)
            return jsonify({"success": True, "mode": mode})
        except ValueError as e:
            return jsonify({"success": False, "error": str(e)}), 400
    return jsonify({"success": False, "error": "Client not available"}), 500


# ---------------------------------------------------------------------------
# Task / design plan management
# ---------------------------------------------------------------------------

@api.route("/api/tasks", methods=["GET"])
def get_tasks():
    """Get the current design plan."""
    _bridge, _ms, cc = _components()
    if cc:
        return jsonify(cc.task_manager.to_dict())
    return jsonify({
        "title": "", "tasks": [], "progress": {},
        "current_step": -1, "is_complete": False,
    })


@api.route("/api/tasks", methods=["POST"])
def create_plan():
    """Create a new design plan."""
    _bridge, _ms, cc = _components()
    data = request.get_json(silent=True) or {}
    title = data.get("title", "Design Plan")
    steps = data.get("steps", [])
    if cc and steps:
        cc.create_design_plan(title, steps)
        return jsonify(cc.task_manager.to_dict())
    return jsonify({"success": False, "error": "No steps provided"}), 400


@api.route("/api/tasks/<int:index>", methods=["PATCH"])
def update_task(index):
    """Update a task step status."""
    _bridge, _ms, cc = _components()
    data = request.get_json(silent=True) or {}
    status = data.get("status", "")
    result = data.get("result", "")
    if cc:
        return jsonify(cc.update_task(index, status, result))
    return jsonify({"success": False}), 500


@api.route("/api/tasks", methods=["DELETE"])
def clear_tasks():
    """Clear all tasks."""
    _bridge, _ms, cc = _components()
    if cc:
        cc.task_manager.clear()
    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# Checkpoint management
# ---------------------------------------------------------------------------

@api.route("/api/checkpoints", methods=["GET"])
def list_checkpoints():
    """List all design checkpoints."""
    _bridge, _ms, cc = _components()
    if cc:
        return jsonify({"checkpoints": cc.list_checkpoints()})
    return jsonify({"checkpoints": []})


@api.route("/api/checkpoints", methods=["POST"])
def save_checkpoint():
    """Save a design checkpoint at the current state."""
    _bridge, _ms, cc = _components()
    data = request.get_json(silent=True) or {}
    name = data.get("name", "")
    description = data.get("description", "")
    if not name:
        return jsonify({"success": False, "error": "Name is required"}), 400
    if cc:
        cp = cc.save_checkpoint(name, description)
        return jsonify({"success": True, "checkpoint": cp})
    return jsonify({"success": False, "error": "Client not available"}), 500


@api.route("/api/checkpoints/<name>/restore", methods=["POST"])
def restore_checkpoint(name):
    """Restore to a previously saved design checkpoint."""
    _bridge, _ms, cc = _components()
    if cc:
        result = cc.restore_checkpoint(name)
        return jsonify(result)
    return jsonify({"success": False, "error": "Client not available"}), 500


@api.route("/api/checkpoints/<name>", methods=["DELETE"])
def delete_checkpoint(name):
    """Delete a design checkpoint."""
    _bridge, _ms, cc = _components()
    if cc:
        deleted = cc.checkpoint_manager.delete(name)
        return jsonify({"success": deleted})
    return jsonify({"success": False}), 500


# ---------------------------------------------------------------------------
# Rules management
# ---------------------------------------------------------------------------

@api.route("/api/rules", methods=["GET"])
def get_rules():
    """List all rule files."""
    from ai.rules_loader import list_rule_files
    return jsonify({"rules": list_rule_files()})


# ---------------------------------------------------------------------------
# LLM Provider management
# ---------------------------------------------------------------------------

@api.route("/api/providers", methods=["GET"])
def list_providers():
    """List available LLM providers with their status."""
    _bridge, _ms, cc = _components()
    if cc:
        return jsonify({
            "providers": cc.provider_manager.list_providers(),
            "active": cc.provider_manager.active_type,
        })
    return jsonify({"providers": [], "active": "anthropic"})


@api.route("/api/providers/<provider_type>", methods=["POST"])
def switch_provider(provider_type):
    """Switch the active LLM provider."""
    _bridge, _ms, cc = _components()
    if cc:
        try:
            result = cc.switch_provider(provider_type)
            return jsonify({"success": True, **result})
        except ValueError as e:
            return jsonify({"success": False, "error": str(e)}), 400
    return jsonify({"success": False, "error": "Client not available"}), 500


@api.route("/api/providers/<provider_type>/models", methods=["GET"])
def list_provider_models(provider_type):
    """List models for a given provider."""
    _bridge, _ms, cc = _components()
    if cc:
        models = cc.provider_manager.list_models(provider_type)
        return jsonify({"models": models, "provider": provider_type})
    return jsonify({"models": [], "provider": provider_type})


@api.route("/api/providers/ollama/status", methods=["GET"])
def ollama_status():
    """Check whether Ollama is running and reachable."""
    _bridge, _ms, cc = _components()
    if cc:
        ollama = cc.provider_manager.get_provider("ollama")
        return jsonify({"available": ollama.is_available() if ollama else False})
    return jsonify({"available": False})
