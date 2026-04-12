# Artifex360 -- Feature Tracker

> AI-powered design intelligence for Fusion 360 -- designs, manipulates, and operates Fusion 360 proficiently through Claude.

---

## Version History

### v0.1.0 -- Initial Scaffold [complete]
- [x] Basic `main.py` with simulated MCP server
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

### v0.9.0 -- Agent Intelligence + Platform Optimization [complete]
- [x] Agent verification loop -- pre/post state comparison, delta tracking in tool results
- [x] 6 new geometric query tools: `get_body_properties`, `get_sketch_info`, `get_face_info`, `measure_distance`, `get_component_info`, `validate_design`
- [x] Error classification system (7 error types: geometry, reference, parameter, script, connection, API, timeout)
- [x] Auto-undo recovery for failed geometry operations
- [x] Script error parsing with line number and error type extraction
- [x] Enriched error payloads with suggestions and recovery guidance
- [x] Verification, error recovery, and querying protocol in system prompt
- [x] `docs/AGENT_INTELLIGENCE.md` design document
- [x] 28 error classifier tests
- [x] Platform optimization -- macOS Silicon (ARM64) + Windows compatibility
- [x] Resilient async runtime: eventlet -> gevent -> threading fallback cascade
- [x] Cross-platform export path resolution (`~/Documents/Fusion360MCP_Exports/`)
- [x] Cross-platform add-in installer (`scripts/install_addin.py`)
- [x] Platform info in status API
- [x] `.env` file support for API key via python-dotenv
- [x] Dark/Light theme switching with CSS variables
- [x] Design history timeline visualization (auto-refreshes after geometry operations)
- [x] Secure API key storage (base64 obfuscation + environment variable priority)
- [x] Multi-document support: 4 new tools (`list_documents`, `switch_document`, `new_document`, `close_document`)
- [x] Document selector UI in top bar
- [x] Total MCP tools: 37

### v1.0.0 -- Agent Intelligence Layer (Roo Code Patterns) [complete]
- [x] Context management / conversation condensation (65% threshold, LLM + rule-based summarization)
- [x] Tool repetition detection (identical + similar call patterns)
- [x] CAD mode system (7 modes: full, sketch, modeling, assembly, analysis, export, scripting)
- [x] Tool grouping (10 groups with mode-based filtering)
- [x] Task decomposition / design plan tracking (create plan, start/complete/fail/skip steps)
- [x] Design checkpoint system (save/restore linked to F360 timeline + conversation state)
- [x] Layered rules/instructions (`config/rules/`, `.f360-rules/`, `config/rules-{mode}/`)
- [x] Example rule files for user guidance
- [x] Mode selector UI in top bar
- [x] Task plan visualization in sidebar
- [x] Checkpoint REST API (save, restore, list, delete)
- [x] Mode-specific system prompt additions
- [x] Task plan context injection into system prompt
- [x] Mode-aware tool filtering in API calls
- [x] 391 total passing tests across 14 test files

### v1.1.0 -- Multi-Provider Support [complete]
- [x] Provider abstraction layer (`ai/providers/base.py`, `LLMResponse` standard format)
- [x] Anthropic provider (`ai/providers/anthropic_provider.py`) with streaming
- [x] Ollama provider (`ai/providers/ollama_provider.py`) via OpenAI-compatible API
- [x] Provider manager for switching between backends
- [x] Anthropic-to-OpenAI message format conversion (tool_use, tool_result, images)
- [x] Ollama tool/function calling support (llama3.1, qwen2.5, mistral, etc.)
- [x] Provider selection UI with tab switcher in settings panel
- [x] Ollama connection status indicator
- [x] Ollama model discovery (auto-refresh from running instance)
- [x] Settings persistence for provider, ollama_base_url, ollama_model
- [x] REST API: /api/providers, /api/providers/{type}/models, /api/providers/ollama/status
- [x] 435 total passing tests across 15 test files

### v1.2.0 -- Real-World Testing Bug Fixes [complete]

#### P0 Critical Fixes
- [x] Fixed `undo`/`redo` tools -- replaced broken `executeTextCommand("Commands.Undo")` with reliable timeline-based approach (`design.timeline.markerPosition`)
- [x] Added 10 pre-loaded type shortcuts to `execute_script` scope: `Point3D`, `Vector3D`, `Matrix3D`, `ObjectCollection`, `ValueInput`, `FeatureOperations`, `BRepBody`, `TemporaryBRepManager`, `math`
- [x] Fixed `create_sphere` -- rewrote with reliable `addByThreePoints` arc + revolve approach
- [x] Increased Ollama timeout from 120s to 300s; configurable via `configure(timeout=...)`; fast-fail sync fallback (30s max)

#### P1 Improvements
- [x] New `delete_body` tool for cleaning up failed geometry (38 total MCP tools)
- [x] Fixed `save_document` for unsaved documents (graceful handling with user message)
- [x] Primitive tools now return actual body name from Fusion 360 (not just requested name)
- [x] Stronger repetition enforcement -- identical tool call loops now inject forced stop + explanation request
- [x] Added "Common Import Mistakes" guide and pre-loaded variables reference to skill document and system prompt
- [x] Added scripting protocol to system prompt with correct usage patterns

#### Infrastructure
- [x] Log sanitizer strips API keys from all log output and saved conversations
- [x] Logs and conversations tracked in repo (gitignore updated)
- [x] Diagnostic startup banner (version, platform, async mode, provider)
- [x] Default port changed from 5000 to 8080
- [x] Fixed gevent/eventlet crash on startup (catch all exceptions, not just ImportError)
- [x] Fixed settings persistence bug (Ollama model field name mismatch)
- [x] Fixed simulation mode forced despite add-in being active (bridge reset on connect)

### v1.3.0 -- Autonomous Agent + Rebranding [complete]

#### Autonomous Action Protocol
- [x] Complete rewrite of `CORE_IDENTITY` in system prompt (`ai/system_prompt.py`)
- [x] `## CRITICAL: Autonomous Action Protocol` placed at the very top of the prompt
- [x] Protocol rule: "NEVER describe what you will do -- DO IT. Every response MUST contain at least one tool_use block"
- [x] Condensed all behavioral protocols (VERIFICATION, ERROR_RECOVERY, TASK_DECOMPOSITION, SCRIPTING, GEOMETRIC_QUERYING) into streamlined format
- [x] Anti-patterns section with explicit examples of what NOT to do
- [x] Quality standards: designs should be detailed and refined, not bare minimum

#### Auto-Continue Mechanism
- [x] New `_has_action_intent()` method in `ai/claude_client.py` with 5 compiled regex patterns
- [x] Detects intent-without-action phrases: "I will now...", "Let me...", "I'll create...", "Next, I...", "Going to..."
- [x] Auto-injects nudge message when agent expresses intent but sends no tool calls
- [x] Maximum 2 auto-continues per turn (`_MAX_AUTO_CONTINUES = 2`) to prevent infinite loops
- [x] `_ACTION_INTENT_PATTERNS` compiled at module level for performance

#### Requirements Clarification
- [x] `## CRITICAL: Requirements Clarification` section in system prompt (`ai/system_prompt.py`)
- [x] Agent asks clarifying questions for vague or ambiguous requests before acting
- [x] Prevents wasted tool calls on underspecified designs

#### Project Identity: Artifex360
- [x] Chose name "Artifex360" (Latin: craftsman + 360 for Fusion 360)
- [x] Tagline: "AI-powered design intelligence for Fusion 360"
- [x] Rebranded 21 files: README, FEATURES, HTML, main.py banner, system prompt identity, all docs, manifest, JS, CSS, settings, routes, events, modes, install script
- [x] Agent identifies as "Artifex360" in conversations
- [x] Zero remaining "Fusion 360 MCP" references in source code
- [x] Add-in filenames preserved (`Fusion360MCP.py`, `Fusion360MCP.manifest`) to avoid breaking F360 loader

#### Root Cause: Agent Freeze Diagnosis
- [x] Identified root cause of agent freezes: text-only planning turns where the LLM expressed intent without calling any tools
- [x] Agent would say "I will now create..." but not actually invoke tools, then wait for user input
- [x] Resolved by the Autonomous Action Protocol + Auto-Continue Mechanism above

---

## Backlog (future)

- [ ] Export preview (3D viewer in browser)
- [ ] Plugin marketplace for community tools
- [ ] Auto-update mechanism
- [ ] Interactive timeline (click-to-rollback via checkpoints)
- [ ] Internet search for design references

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
- The web app runs at `localhost:8080` and communicates with the Fusion 360 add-in over a local bridge.
- Claude uses the Anthropic tool-use API for a full agent loop: reasoning, tool calls, observation, repeat.
- Multimodal support allows Claude to receive viewport screenshots as image content blocks.
