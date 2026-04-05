# Fusion 360 MCP Controller — Feature Tracker

## Version History

### v0.1.0 — Initial Scaffold (2026-04-04)
- [x] Basic `main.py` with simulated Fusion 360 MCP server
- [x] Simulated Claude client with keyword-based command parsing
- [x] CLI loop for basic interaction

---

## v0.2.0 — Modular Architecture + UI (In Progress)

### Core Infrastructure
- [x] Project modularized into `mcp/`, `fusion/`, `ai/`, `config/`, `ui/` packages
- [x] `requirements.txt` with all dependencies
- [x] `config/settings.py` — persistent settings with JSON storage
- [x] `mcp/server.py` — MCP server with tool registration
- [x] `fusion/bridge.py` — Fusion 360 API bridge (real + simulation mode)
- [x] `ai/claude_client.py` — Anthropic Claude API client with MCP tool use

### UI
- [x] `ui/app.py` — Main tkinter application window
- [x] `ui/chat_panel.py` — Chat interface with message history
- [x] `ui/settings_panel.py` — Settings panel (API key, model, security)
- [x] `ui/status_panel.py` — Status/log panel with live output

### Entry Point
- [x] `main.py` refactored as clean entry point

---

## Backlog / Planned Features

### AI / MCP
- [ ] Stream Claude responses token-by-token into chat UI
- [ ] Tool call visualization (show what Fusion command is being called)
- [ ] Conversation history persistence (save/load sessions)
- [ ] System prompt customization in settings
- [ ] Multi-turn context window management

### Fusion 360 Integration
- [ ] `create_sphere` tool
- [ ] `create_sketch` tool (lines, arcs, splines)
- [ ] `add_fillet` / `add_chamfer` tools
- [ ] `mirror_body` / `pattern_body` tools
- [ ] `export_stl` / `export_step` tools
- [ ] `get_body_list` — list all bodies in design
- [ ] `select_body` — select a body by name
- [ ] `apply_material` — assign material to body
- [ ] `create_component` — create sub-component
- [ ] `undo` / `redo` commands
- [ ] Screenshot/viewport capture and send to Claude for visual feedback

### UI / UX
- [ ] Dark mode theme
- [ ] Resizable panels (splitter layout)
- [ ] Syntax highlighting for code blocks in chat
- [ ] Copy-to-clipboard button on AI responses
- [ ] Prompt history (up/down arrow navigation)
- [ ] Token usage display (input/output/cost estimate)
- [ ] Connection status indicator (Fusion 360 connected/disconnected)
- [ ] Notification/toast system for action results

### Security / Settings
- [ ] API key stored in macOS Keychain (not plain JSON)
- [ ] Rate limiting / max tokens per request setting
- [ ] Allowed commands whitelist (restrict what AI can do)
- [ ] Confirmation dialog before destructive operations

### DevOps / Distribution
- [ ] `setup.py` / `pyproject.toml` for packaging
- [ ] macOS `.app` bundle via `py2app`
- [ ] Auto-update checker
- [ ] Logging to file with rotation

---

## Bug Tracker

| ID | Status | Description | Version Found |
|----|--------|-------------|---------------|
| B001 | Open | Fusion 360 API import fails outside Fusion environment — handled via simulation mode | v0.1.0 |
| B002 | Open | No real Claude API call in v0.1.0 — only keyword matching | v0.1.0 |

---

## Notes
- Fusion 360 API (`adsk.*`) is only available when running **inside** Fusion 360 as an add-in. The bridge module handles both modes gracefully.
- Claude API requires an Anthropic API key set in Settings panel or `config/config.json`.
