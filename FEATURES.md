# Fusion 360 MCP Agent -- Feature Tracker

> AI agent system that designs, manipulates, and operates Fusion 360 proficiently through Claude.

---

## Version History

### v0.1.0 -- Initial Scaffold [complete]
- [x] Basic `main.py` with simulated Fusion 360 MCP server
- [x] Simulated Claude client with keyword-based command parsing
- [x] CLI loop for basic interaction

### v0.2.0 -- Modular Architecture + Tkinter UI [complete, superseded]

> **Note:** The Tkinter desktop GUI has been superseded by the Flask web application introduced in v0.3.0. This milestone remains for historical reference.

#### Core Infrastructure
- [x] Project modularized into `mcp/`, `fusion/`, `ai/`, `config/`, `ui/` packages
- [x] `requirements.txt` with all dependencies
- [x] `config/settings.py` -- persistent settings with JSON storage
- [x] `mcp/server.py` -- MCP server with tool registration
- [x] `fusion/bridge.py` -- Fusion 360 API bridge (real + simulation mode)
- [x] `ai/claude_client.py` -- Anthropic Claude API client with MCP tool use

#### UI (Tkinter -- superseded)
- [x] `ui/app.py` -- Main tkinter application window
- [x] `ui/chat_panel.py` -- Chat interface with message history
- [x] `ui/settings_panel.py` -- Settings panel (API key, model, security)
- [x] `ui/status_panel.py` -- Status/log panel with live output

#### Entry Point
- [x] `main.py` refactored as clean entry point

### v0.3.0 -- Flask Web App + Agent Foundation [complete]
- [x] Architecture design document (`docs/ARCHITECTURE.md`)
- [x] Flask app with Socket.IO (replace Tkinter)
- [x] Browser chat UI with Tailwind CSS
- [x] WebSocket event protocol (`text_delta`, `tool_call`, `tool_result`, etc.)
- [x] Settings panel in web UI (API key, model, system prompt, simulation mode)
- [x] Connection status indicator
- [x] Refactor `ai/claude_client.py` to emit Socket.IO events
- [x] Update `main.py` entry point for Flask
- [x] Update `requirements.txt` (Flask, Flask-SocketIO, etc.)

### v0.4.0 -- Screenshot + Vision [complete]
- [x] `take_screenshot` command in F360 add-in (viewport capture to PNG/base64)
- [x] Bridge support for screenshot forwarding
- [x] Screenshot display inline in browser chat
- [x] Send screenshots to Claude as image content blocks (multimodal)
- [x] Auto-screenshot after geometry tool execution

### v0.5.0 -- Dynamic Script Execution [complete]
- [x] `execute_script` MCP tool
- [x] Add-in script execution handler with sandbox (timeout, restricted imports)
- [x] Script output capture (stdout, stderr, return values)
- [x] Claude writes and executes custom F360 scripts autonomously

### v0.6.0 -- Comprehensive F360 Skill Document [complete]
- [x] `docs/F360_SKILL.md` -- complete reference for Claude
- [x] API patterns: sketch-profile-feature workflow
- [x] Common operations cookbook (extrude, revolve, fillet, chamfer, etc.)
- [x] Best practices for parametric design
- [x] Error handling patterns
- [ ] Material and appearance reference

### v0.7.0 -- Expanded MCP Tools [complete]
- [x] Sketch tools: `create_sketch`, `add_sketch_line`, `add_sketch_circle`, `add_sketch_arc`, `add_sketch_rectangle`
- [x] Feature tools: `extrude`, `revolve`, `add_fillet`, `add_chamfer`
- [x] Body tools: `mirror_body`, `create_component`, `apply_material`
- [x] Export tools: `export_stl`, `export_step`, `export_f3d`
- [x] Utility tools: `get_timeline`, `set_parameter`, `redo`
- [x] Add-in handlers for all new tools

### v0.8.0 -- Agent Intelligence + Polish [complete]
- [x] System prompt engineering with F360 skill document
- [x] Conversation persistence to disk (JSON)
- [x] Conversation management UI (new/load/delete)
- [x] Token usage tracking and display
- [x] Streaming responses (token-by-token via `messages.stream()`)
- [x] Rate limiting enforcement
- [x] Confirmation dialogs for destructive operations

---

## Backlog (future)

- [ ] Dark mode / theme switching
- [ ] Multi-document support
- [ ] Design history visualization
- [ ] Undo/redo visualization in timeline
- [ ] Export preview (3D viewer in browser)
- [ ] Plugin marketplace for community tools
- [ ] Secure API key storage (keychain)
- [ ] Auto-update mechanism
- [ ] Agent can use internet to find information 

---

## Bug Tracker

| ID   | Status   | Description                                                                        | Version Found |
|------|----------|------------------------------------------------------------------------------------|---------------|
| B001 | Resolved | Fusion 360 API import fails outside Fusion environment -- handled via simulation mode | v0.1.0        |
| B002 | Resolved | No real Claude API call in v0.1.0 -- only keyword matching (resolved in v0.2.0)      | v0.1.0        |

---

## Notes

- Fusion 360 API (`adsk.*`) is only available when running **inside** Fusion 360 as an add-in. The bridge module handles both modes gracefully.
- Claude API requires an Anthropic API key configured in the web UI settings panel or `config/config.json`.
- The web app runs at `localhost:5000` and communicates with the Fusion 360 add-in over a local bridge.
- Claude uses the Anthropic tool-use API for a full agent loop: reasoning, tool calls, observation, repeat.
- Multimodal support allows Claude to receive viewport screenshots as image content blocks.
