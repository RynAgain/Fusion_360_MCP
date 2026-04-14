# Artifex360 -- Feature Tracker

> AI-powered design intelligence for Fusion 360 -- designs, manipulates, and operates Fusion 360 proficiently through Claude.

---

## Code Review -- Prioritized Task Backlog

**Review conducted: 2026-04-14**

**Status: All 45 tasks implemented (2026-04-14). Version bumped to 1.5.0. 503 tests passing.**

This review covered all source files, test suites, conversation logs (`data/conversations/`), and the existing bug tracker below. Findings are organized by severity. Every item is actionable -- no "consider maybe possibly" fluff. If something is listed here, it needs fixing.

Severity levels:
- **P0** -- Security or data-loss risk. Fix immediately.
- **P1** -- Critical bug affecting core functionality. Fix this sprint.
- **P2** -- Important improvement with clear user impact. Fix soon.
- **P3** -- Code quality, cleanup, or minor improvement. Fix when possible.

---

### P0 -- SECURITY / DATA LOSS (fix immediately)

#### **[DONE]** TASK-001: Remote Code Execution via execute_script -- No Sandboxing
- **Files:** `fusion_addin/addin_server.py:479-560`
- **Problem:** The `_execute_script` handler calls bare `exec()` on arbitrary Python code with access to the entire Fusion 360 API, filesystem (`os`, `math`, `json` injected), and `__builtins__`. Zero sandboxing. Any local process that can reach the TCP port can delete files, exfiltrate data, or brick Fusion.
- **Fix:** Strip `__builtins__`, deny `os`/`sys`/`subprocess`/`importlib`. Better: restricted namespace or explicit user confirmation before execution.

#### **[DONE]** TASK-002: Unauthenticated TCP Server
- **Files:** `fusion_addin/addin_server.py:79-102`
- **Problem:** TCP server on `127.0.0.1:9876` accepts connections with zero authentication. Any local process can send arbitrary Fusion 360 commands including `execute_script`. Combined with TASK-001, this is full local RCE.
- **Fix:** Shared secret / token exchange at connection time. Minimum: challenge-response handshake.

#### **[DONE]** TASK-003: Debug Mode + Werkzeug Debugger Exposed to Network
- **Files:** `main.py:149`
- **Problem:** `socketio.run(..., debug=True, allow_unsafe_werkzeug=True)` enables the interactive Werkzeug debugger. Combined with `0.0.0.0` binding (line 139), any machine on the network can execute arbitrary Python.
- **Fix:** Default `debug=False`, `allow_unsafe_werkzeug=False`. Use env var to enable debug only in development. Default host to `127.0.0.1`.

#### **[DONE]** TASK-004: Hardcoded Flask Secret Key
- **Files:** `web/app.py:70`
- **Problem:** `SECRET_KEY = os.environ.get("SECRET_KEY", "fusion-mcp-dev-key")`. Fallback is static. Session cookies are trivially forgeable.
- **Fix:** Generate random secret at first run and persist it, or refuse to start without `SECRET_KEY` set.

#### **[DONE]** TASK-005: Path Traversal in Conversation Persistence
- **Files:** `ai/conversation_manager.py:72,98,136`
- **Problem:** `conversation_id` is used directly in file paths with no validation. A malformed ID like `../../etc/passwd` writes/reads/deletes arbitrary files.
- **Fix:** Validate `conversation_id` matches UUID pattern before constructing paths.

#### **[DONE]** TASK-006: Path Traversal in Rules Loader
- **Files:** `ai/rules_loader.py:40`
- **Problem:** `mode` parameter used in directory path with no validation. `mode="../../etc"` escapes config directory.
- **Fix:** Validate `mode` against a whitelist or restrict to alphanumeric characters.

#### **[DONE]** TASK-007: Path Traversal in Export Functions
- **Files:** `fusion/bridge.py:460-469`, `fusion_addin/addin_server.py:964-979`
- **Problem:** `_resolve_export_path` resolves absolute paths as-is. Agent could write to arbitrary filesystem locations.
- **Fix:** Validate resolved path is under exports directory. Reject absolute paths or paths with `..`.

#### **[DONE]** TASK-008: API Key Double-Encoding on Update
- **Files:** `config/settings.py:86-100`
- **Problem:** `Settings.update()` base64-encodes `anthropic_api_key` before storing. If called with already-encoded value (round-trip), key gets double-encoded: `enc:enc:base64(base64(...))`. The `api_key` property only strips one `enc:` prefix.
- **Fix:** Check if value already starts with `enc:` before encoding, or encode only on `save()`.

#### **[DONE]** TASK-009: CORS Wildcard on Socket.IO
- **Files:** `web/app.py:79`
- **Problem:** `cors_allowed_origins="*"` allows any website to open a WebSocket and send commands including `user_message` which triggers the AI agent loop. Malicious webpage could instruct Claude to run destructive Fusion 360 operations.
- **Fix:** Restrict to `http://localhost:*` or configured host/port only.

---

### P1 -- CRITICAL BUGS (fix this sprint)

#### **[DONE]** TASK-010: Recursive Context Condensation Corruption
- **Files:** `ai/context_manager.py` (condense method)
- **Evidence:** Conversation logs `6bbae3aa`, `b059ad35` show condensation summaries embedding previous summaries as "user requests", creating deeply nested useless text like `[Condensation #20] > [Condensation #19] > [Condensation #18]...`. After 20+ condensations the summary is mostly nested boilerplate.
- **Fix:** Detect and strip previous condensation summaries before creating new ones. Never include a condensation summary as a "user request".

#### **[DONE]** TASK-011: LLM-Based Condensation is Dead Code
- **Files:** `ai/context_manager.py:213,228-242`
- **Problem:** `_llm_summarize` checks `hasattr(client, "client") and client.client` but `ClaudeClient` has no `.client` attribute. It uses `self.provider_manager.active`. This code path always returns `None`, meaning LLM-based condensation silently never works.
- **Fix:** Use `client.provider_manager.active.create_message(...)` instead of non-existent `client.client`.

#### **[DONE]** TASK-012: Thread Safety Disaster in ClaudeClient._run_turn()
- **Files:** `ai/claude_client.py:612-1167`
- **Problem:** Operates on local copy of `self.conversation_history` but multiple code paths write back at different points while also mutating the local list in-place. No re-entrant guard prevents concurrent `send_message` calls. Daemon threads can corrupt conversation history.
- **Fix:** Add per-turn mutex or reject concurrent calls. Wrap `_run_turn` in `self._lock`.

#### **[DONE]** TASK-013: execute_script Returns Success Despite Internal Failures
- **Files:** `fusion_addin/addin_server.py:479-560`
- **Evidence:** Conv `b059ad35` -- tool returns `success: true` when 2 of 3 cut operations failed internally. Script-level success (did not crash) conflated with operation-level success (all operations completed).
- **Fix:** Detect and report partial failures. If script prints errors or raises caught exceptions, reflect that in the tool result status.

#### **[DONE]** TASK-014: Agent Goes Silent / Drops User Messages
- **Files:** `ai/claude_client.py`, `web/events.py`
- **Evidence:** Conv `6bbae3aa` ends with two user messages and zero assistant responses. Agent crashed or context exhausted with no error feedback.
- **Fix:** Wrap agent loop in error handler that always emits a response (even an error message) to the user. Never leave a user message unanswered.

#### **[DONE]** TASK-015: Cancellation is a Stub
- **Files:** `web/events.py:110-117`
- **Problem:** The `cancel` event handler just logs and emits a message. The agent loop has no cancellation mechanism. Once started, it runs to completion regardless.
- **Fix:** Implement `threading.Event` checked between tool calls in the agent loop.

#### **[DONE]** TASK-016: Default Model ID is Invalid
- **Files:** `config/settings.py:22`
- **Problem:** Default model `"claude-opus-4-5"` is not a valid Anthropic model ID. Valid IDs in `ai/providers/anthropic_provider.py:16` are `claude-sonnet-4-20250514`, `claude-opus-4-20250514`, etc. Fresh installations will fail immediately.
- **Fix:** Use a valid model ID as default.

#### **[DONE]** TASK-017: Checkpoint Restore is Not Atomic
- **Files:** `ai/checkpoint_manager.py:77-138`, `ai/claude_client.py:446-453`
- **Problem:** `restore()` performs destructive F360 timeline rollback, but conversation truncation happens separately in the caller. If an exception occurs between rollback and truncation, timeline is rolled back but conversation is not -- inconsistent state.
- **Fix:** Make restore atomic. Either accept and mutate the list in `restore()`, or wrap the entire operation in a lock with rollback on failure.

#### **[DONE]** TASK-018: Reopen Bugs B007 (False Success Claims) and B010 (Ollama Failures)
- **Files:** Various
- **Evidence:** B007 still manifesting in conv `827d26df` (agent claims success despite user-visible failures). B010 still manifesting in conv `98163763` (zero responses from Ollama).
- **Fix:** Reopen these bugs. B007 needs post-operation verification that actually gates the success response. B010 needs investigation of why Ollama sessions produce zero output.

---

### P2 -- IMPORTANT IMPROVEMENTS (fix soon)

#### **[DONE]** TASK-019: Cut Operations Fail ~85% Due to Missing participantBodies
- **Files:** `fusion_addin/addin_server.py` (cut handlers), system prompt
- **Evidence:** Most common error across all sessions. "No target body found to cut or intersect!" because agent scripts do not set `cutInput.participantBodies`. The v1.4.0 pre-cut validation was supposed to fix this but is not effective in practice.
- **Fix:** Default `participantBodies` to all visible bodies in the component when not explicitly set. Add to system prompt as mandatory pattern.

#### **[DONE]** TASK-020: Ambiguous Body Name Resolution
- **Files:** `fusion_addin/addin_server.py` (body lookup), `fusion/bridge.py`
- **Evidence:** Conv `6bbae3aa` -- `get_body_properties("LED_Matrix_Frame")` matches tiny artifact body instead of the real `LED_Matrix_Frame (1)`. Name resolution is first-match, not smart.
- **Fix:** When multiple bodies share a name prefix, return all matches with dimensions. Let agent disambiguate.

#### **[DONE]** TASK-021: Agent Cannot Determine Undo Depth
- **Files:** `ai/claude_client.py`, system prompt
- **Evidence:** Conv `827d26df` -- agent calls undo 3 times blindly until force-stopped. No mechanism to specify target timeline position.
- **Fix:** Expose timeline position in design state. Let agent undo to a specific position, not just "undo once".

#### **[DONE]** TASK-022: Repetition Warnings Still Ineffective
- **Files:** `ai/repetition_detector.py`, `ai/claude_client.py`
- **Evidence:** Conv `6bbae3aa` -- `execute_script` called 5-6 times with warnings ignored by agent. v1.2.0 was supposed to fix this.
- **Fix:** After N warnings, inject a hard stop that forces the agent to explain its approach before continuing. Current warning is just text the agent ignores.

#### **[DONE]** TASK-023: _truncate() Breaks tool_use/tool_result Pairing
- **Files:** `ai/context_manager.py:202-207`
- **Problem:** `_truncate` blindly chops the message list in half. If the split lands between a `tool_use` assistant message and its `tool_result` user message, the Anthropic API rejects the conversation. `_find_safe_split_point` exists but is only used in `condense()`, not `_truncate()`.
- **Fix:** Reuse `_find_safe_split_point` in `_truncate()`.

#### **[DONE]** TASK-024: Image Token Estimation Off by 36x
- **Files:** `ai/context_manager.py:74-75`
- **Problem:** Base64 image size / 3 gives ~58K estimated tokens for a screenshot. Anthropic charges ~1600 tokens per image. Overestimation by 36x causes premature condensation.
- **Fix:** Use flat estimate per image (~1600 tokens for standard resolution).

#### **[DONE]** TASK-025: Bare `except Exception: pass` Swallows Critical Errors
- **Files:** `ai/claude_client.py:1076`, `ai/design_state_tracker.py:99-100`, `ai/checkpoint_manager.py:100-101`
- **Problem:** Verification data silently lost. User and agent have no idea verification failed.
- **Fix:** `logger.exception(...)` instead of `pass`. Add `"verification_error"` key to result dict.

#### **[DONE]** TASK-026: `datetime.utcnow()` Deprecated
- **Files:** `ai/conversation_manager.py:62`
- **Problem:** Deprecated since Python 3.12, returns naive datetime. Rest of codebase correctly uses `datetime.now(timezone.utc)`.
- **Fix:** Replace with `datetime.now(timezone.utc).isoformat()`.

#### **[DONE]** TASK-027: Sphere Position Parameter Completely Ignored
- **Files:** `fusion_addin/addin_server.py:350-418`
- **Problem:** `position` is extracted but never used. Sphere always created at origin.
- **Fix:** Translate sketch points by position offset or use move body after creation.

#### **[DONE]** TASK-028: Cylinder Ignores Z Position
- **Files:** `fusion_addin/addin_server.py:285-314`
- **Problem:** Z component from `position` is silently discarded. Sketch created on XY plane always.
- **Fix:** Create offset construction plane for non-zero Z, or document the limitation.

#### **[DONE]** TASK-029: Settings Module-Level Singleton Loads at Import Time
- **Files:** `config/settings.py:185`
- **Problem:** `settings = Settings()` executes `self.load()` at import time. Malformed config or slow disk blocks/crashes all imports. Tests cannot control lifecycle.
- **Fix:** Lazy initialization or explicit `settings.load()` call.

---

### P3 -- CLEANUP / QUALITY (improve when possible)

#### **[DONE]** TASK-030: Delete Deprecated Tkinter UI
- **Files:** `ui/app.py`, `ui/chat_panel.py`, `ui/settings_panel.py`, `ui/status_panel.py`
- **Problem:** ~750 lines of dead code. `ui/DEPRECATED.md` says it is deprecated. It is in git history.
- **Fix:** Delete the entire `ui/` directory except `ui/__init__.py`.

#### **[DONE]** TASK-031: Confirmation Modal is Display-Only
- **Files:** `web/static/js/app.js:1631-1639`
- **Problem:** "Require Confirmation" shows a modal but does not gate tool execution. Modal has "Dismiss" but no "Allow"/"Deny". Tool executes regardless. Pre-hook exists only in deprecated Tkinter UI.
- **Fix:** Wire the pre-hook mechanism into the web UI. Block tool execution until user confirms.

#### **[DONE]** TASK-032: No Integration Tests for Agent Loop
- **Files:** `tests/`
- **Problem:** `tests/test_claude_client.py` covers init and config but never calls `_run_turn` or `send_message`. The most important code path has zero test coverage. All bridge tests only exercise simulation mode.
- **Fix:** Add integration tests that mock the provider but exercise the full turn loop.

#### **[DONE]** TASK-033: `_run_claude_loop` Calls Private Method `_run_turn`
- **Files:** `web/events.py:191`
- **Problem:** Calling `claude_client._run_turn()` from outside the class. Fragile coupling.
- **Fix:** Rename to `run_turn()` (public API) or provide a public wrapper.

#### **[DONE]** TASK-034: Thread Safety of simulation_mode Flag
- **Files:** `fusion/bridge.py:67-68,140`
- **Problem:** `simulation_mode` read without lock in command methods, written under lock in connect, written without lock on socket error. Data race between threads.
- **Fix:** Always read/write under `self._lock` or use `threading.Event`.

#### **[DONE]** TASK-035: Version String in Three Places
- **Files:** `main.py:119`, `ui/settings_panel.py:207`, `fusion_addin/Fusion360MCP.manifest:9`
- **Problem:** `1.2.0` vs `v0.2.0` vs `1.2.0`. Guaranteed to diverge.
- **Fix:** Single source of truth imported everywhere.

#### **[DONE]** TASK-036: Module-Level Globals for Shared State in web/app.py
- **Files:** `web/app.py:20-23`
- **Problem:** `bridge`, `mcp_server`, `claude_client`, `socketio_instance` as module globals. Prevents testing, prevents multiple instances.
- **Fix:** Use `app.extensions` or Flask application context.

#### **[DONE]** TASK-037: `get_settings` Route Exposes Internal `_data` Dict
- **Files:** `web/routes.py:76`
- **Problem:** `data = dict(settings._data)` accesses private attribute. Leaks all internal settings to browser.
- **Fix:** Settings should expose `to_safe_dict()` method.

#### **[DONE]** TASK-038: Ollama `is_available()` Makes Blocking Network Call Every Time
- **Files:** `ai/providers/ollama_provider.py:47-52`
- **Problem:** Hits `http://localhost:11434/api/tags` with 3s timeout on every check. If Ollama unreachable, every message blocks 3s.
- **Fix:** Cache result with 30s TTL.

#### **[DONE]** TASK-039: `ALL_GROUPS` Captures State at Import Time
- **Files:** `ai/modes.py:17`
- **Problem:** `ALL_GROUPS = list(TOOL_GROUPS.keys())` is frozen at import. Plugins adding groups post-import are excluded from "full" mode.
- **Fix:** Query `TOOL_GROUPS.keys()` dynamically.

#### **[DONE]** TASK-040: No Input Validation on Numeric Parameters
- **Files:** `fusion/bridge.py:739-888`
- **Problem:** `radius=-5`, `height=0`, `distance=float('inf')` all pass through to Fusion 360.
- **Fix:** Validate positive-only for dimensions, finite values.

#### **[DONE]** TASK-041: Inconsistent Response Schemas
- **Files:** `fusion_addin/addin_server.py` (various handlers)
- **Problem:** Some return `{"status": "success", "success": True}`, some only `{"success": True}`, some only `{"status": "success"}`.
- **Fix:** Define standard response envelope. Enforce in all handlers.

#### **[DONE]** TASK-042: CDN Dependencies Without Integrity Hashes
- **Files:** `web/templates/index.html:9,497-498`
- **Problem:** Tailwind, Socket.IO, marked.js loaded from CDNs without SRI hashes.
- **Fix:** Add integrity/crossorigin attributes. Or vendor locally.

#### **[DONE]** TASK-043: f-strings in Logging Calls
- **Files:** `ai/rules_loader.py:66-68` and others
- **Problem:** f-strings defeat lazy evaluation in logging.
- **Fix:** Use `logger.debug("msg: %s", var)` format.

#### **[DONE]** TASK-044: Streaming Fallback Catches Too Broadly
- **Files:** `ai/providers/ollama_provider.py:188-196`, `ai/providers/anthropic_provider.py:85-94`
- **Problem:** Catches `Exception` or `(AttributeError, TypeError)` which masks programming errors.
- **Fix:** Catch only `requests.RequestException` for network fallback. Check method existence explicitly.

#### **[DONE]** TASK-045: _create_box Uses Wrong Rectangle Semantics
- **Files:** `fusion_addin/addin_server.py:316-348`
- **Problem:** `addCenterPointRectangle` creates geometry centered on position, but tool describes "position" as "origin of the box". Semantic mismatch confuses the AI.
- **Fix:** Use `addTwoPointRectangle` for origin-corner semantics, or update docs.

---

## Version History

### v1.5.0 -- Security & Quality Release (2026-04-14)
**P0 Security (9 fixes):** Sandboxed execute_script, added TCP auth tokens, disabled debug mode by default, auto-generated Flask secret keys, fixed 3 path traversal vulnerabilities, fixed API key double-encoding, restricted CORS origins.
**P1 Critical Bugs (9 fixes):** Fixed recursive condensation corruption, revived dead LLM condensation code path, added thread safety to agent turn loop, execute_script now detects partial failures, agent always responds even on errors, implemented real cancellation, fixed invalid default model, made checkpoint restore atomic, reopened B007/B010.
**P2 Improvements (11 fixes):** Auto-populate participantBodies for cuts, smart body name disambiguation, multi-step undo with timeline tracking, escalating repetition enforcement with force-stop, safe truncation respecting tool pairs, fixed 36x image token overestimation, replaced silent exception swallowing with logging, fixed deprecated datetime API, sphere/cylinder position now respected, lazy settings loading.
**P3 Cleanup (16 fixes):** Deleted deprecated Tkinter UI, added Allow/Deny confirmation modal, created agent loop integration tests, made run_turn public API, thread-safe simulation_mode, single-source version string, Flask app extensions pattern, safe settings dict exposure, Ollama availability caching, dynamic tool groups, numeric parameter validation, standardized response schemas, CDN crossorigin attrs, lazy log formatting, narrowed streaming exception catches, fixed box rectangle semantics.

### v1.6.0 -- Orchestrated Workflows (2026-04-14)

Adapts Roo Code's orchestrator pattern for coordinated multi-step CAD design workflows.

#### Orchestration Engine
- [x] **Orchestrator Mode** -- New `orchestrator` CadMode with read-only tools (query + vision) that coordinates specialist modes
- [x] **SubtaskManager** -- Executes subtasks by snapshotting ClaudeClient state, switching modes, running the agentic loop in isolation, and restoring state
- [x] **ContextBridge** -- Assembles context packets for subtasks with dependency results, design state, and token budget management
- [x] **Active TaskManager** -- Extended with dependency graphs, mode hints per step, auto-advance, retry logic, and plan summaries

#### Orchestration Features
- [x] `create_orchestrated_plan()` -- Create multi-step plans with dependencies and mode assignments
- [x] `execute_next_subtask()` -- Auto-advance to next ready step and execute
- [x] `execute_subtask(step_index)` -- Execute a specific step
- [x] `execute_full_plan()` -- Run all remaining steps sequentially with retry support
- [x] Quality gates between steps (design state verification)
- [x] Error recovery with configurable retry limits per step

#### System Intelligence
- [x] Orchestration Protocol in system prompt (conditional on orchestrator mode)
- [x] Mode selection heuristics for CAD tasks (sketch/modeling/assembly/analysis/export/scripting)
- [x] Orchestrator rules system (`config/rules-orchestrator/`)

#### Web Layer
- [x] 6 REST endpoints under `/api/orchestration/` (plan CRUD, step execution, status)
- [x] 5 WebSocket events for real-time orchestration (plan creation, step execution, status)
- [x] Background task execution for long-running subtasks

#### Testing
- [x] 42 tests for ContextBridge
- [x] 32 tests for SubtaskManager
- [x] Extended TaskManager tests (43 new)
- [x] Extended ClaudeClient tests (8 new)
- [x] Extended modes tests (8 new)
- [x] Extended system prompt tests (4 new)
- [x] Extended web routes tests (6 new)
- [x] Total: 640 tests passing (up from ~503)

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

### v1.4.0 -- Context Intelligence + Geometry Awareness [complete]

#### Context & State Tracking
- [x] Persistent Design State Tracker -- maintain structured JSON model of current F360 design (bodies, bounding boxes, volumes, sketches, spatial relationships) updated after every tool call, preserved across condensation
- [x] Enhanced pre/post verification delta -- capture bounding boxes, volumes, face counts before/after geometry operations (not just body count)
- [x] Condensation-resistant state preservation -- inject current design state snapshot into condensation summary (body list, dimensions, operation success/failure status, remaining tasks)
- [x] Fix context condensation tool_use/tool_result pairing -- ensure atomic message pairs are never split during condensation (caused Anthropic 400 errors)

#### Geometry Understanding
- [x] Add sketch coordinate system documentation to skill doc -- face-local vs world-space coordinates
- [x] Add profile selection guidance to skill doc -- area-based profile selection when sketching on faces
- [x] Pre-cut validation -- before CutFeatureOperation, verify sketch plane intersects target body by comparing with bounding box
- [x] Mandatory post-operation verification -- after cuts, check face count increased and volume decreased
- [x] Add common API method signatures to skill doc (construction planes, holes, revolve constraints)

#### Screenshot Optimization
- [x] Screenshot budget system -- limit auto-screenshots per conversation turn (e.g., max 3)
- [x] Selective screenshot capture -- only after last geometry op in a batch, or when body count changes
- [x] Reduced resolution for intermediate screenshots

#### Error Recovery
- [x] Automatic diagnostic queries on error -- auto-run `get_sketch_info` for extrude failures, `get_body_list` for reference errors
- [x] Structured alternative suggestions on repetition detection -- when repetition fires, suggest specific alternative tools/approaches
- [x] Variable scope isolation documentation -- prominently document that each `execute_script` runs in isolated scope
- [x] 492 total passing tests across 16 test files

---

## Backlog (future)

- [ ] Export preview (3D viewer in browser)
- [ ] Plugin marketplace for community tools
- [ ] Auto-update mechanism
- [ ] Interactive timeline (click-to-rollback via checkpoints)
- [ ] Internet search for design references

---

## Bug Tracker

| ID   | Status   | Priority | Description                                                                                                                                                                                                                         | Version Found | Source                                       |
|------|----------|----------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------|----------------------------------------------|
| B001 | Resolved | --       | Fusion 360 API import fails outside Fusion environment -- handled via simulation mode                                                                                                                                                | v0.1.0        | --                                           |
| B002 | Resolved | --       | No real Claude API call in v0.1.0 -- only keyword matching (resolved in v0.2.0)                                                                                                                                                      | v0.1.0        | --                                           |
| B003 | Resolved | P0       | Undo command always fails -- `undo` tool uses `Commands.Undo` text command which doesn't exist in F360 API. All 7 undo attempts across sessions failed with `RuntimeError: 3 : There is no command Commands.Undo`. Need timeline rollback. | v1.2.0        | Log lines 220,234,244,413,858,896; Conv 64170a9f |
| B004 | Resolved | P0       | Point3D NameError in `execute_script` -- scripts using bare `Point3D.create()` fail because `exec()` environment doesn't inject `adsk.core` names. Most common error across all sessions (8+ occurrences). Need to inject `Point3D = adsk.core.Point3D` into exec globals or add to skill doc boilerplate. | v1.2.0        | Log lines 272,328,397,649,968; Conv 0d371ce5 |
| B005 | Resolved | P0       | Context condensation breaks tool_use/tool_result pairs -- condensation can split tool_use/tool_result message pairs, causing Anthropic API 400 errors: `tool_use ids were found without tool_result blocks immediately after`.         | v1.3.0        | Log line 784                                 |
| B006 | Resolved | P1       | `get_document_info` crashes on `savePath` -- `AttributeError: 'FusionDocument' object has no attribute 'savePath'` in `addin_server.py`.                                                                                            | v1.2.0        | Conv 0d371ce5 msg 2                          |
| B007 | Reopened | P1       | Agent claims success on failed cut operations -- reports "MISSION ACCOMPLISHED" when body still has 6 faces / 857.73 cm3 volume (solid box). No post-operation verification catches the false success. **Reopened 2026-04-13:** Still manifesting in conversation logs as of 2026-04-13. Agent declares success despite user-visible failures. Partial fix via TASK-013 (execute_script failure detection). | v1.3.0        | Conv 64170a9f final messages; Conv 827d26df  |
| B008 | Resolved | P1       | `save_document` fails on new documents -- `save()` requires prior `saveAs()` for new documents. No handling for this case.                                                                                                           | v1.2.0        | Log lines 317-319                            |
| B009 | Resolved | P2       | Body deletion loop causes index error -- iterating `range(collection.count)` while deleting causes `Bad index parameter`. Need reverse iteration or while-loop pattern.                                                              | v1.3.0        | Conv 64170a9f msg 11                         |
| B010 | Reopened | P2       | Ollama 404 errors silently swallow user messages -- all 3 user messages in conv 20a2d7f3 got no response. No error surfaced to user. **Reopened 2026-04-13:** Ollama sessions produce zero responses as of 2026-04-13 (conv 98163763). | v1.1.0        | Conv 20a2d7f3; Conv 98163763                 |
| B011 | Resolved | P2       | `ConstructionPlaneInput.setByPlane()` API misuse -- agent uses `setByPlane()` with offset parameter (3 args) but method only takes 2. Should use `setByOffset()`.                                                                    | v1.3.0        | Conv 0d371ce5 msg 11                         |
| B012 | Resolved | P2       | Git merge conflict in `fusion_mcp.log` -- unresolved merge conflict markers in log file between Windows and macOS entries.                                                                                                           | v1.2.0        | fusion_mcp.log lines 53-504                  |

---

## Test Session Observations (2026-04-12/13)

### Sessions Analyzed
- **LED Matrix Frame (Conv 0d371ce5)**: L-shaped frame for 192.1mm LED matrix + RPi 4B + Hub75 board + Noctua fan. 28 messages. Partial success -- frame and back cover created, LED slot and mounting holes failed.
- **LED Matrix Frame Extended (Conv 64170a9f)**: Same project, extended session with 4 context condensation cycles. 28 messages. Multiple redesign attempts. Final result was a solid box claimed as complete but cavity never cut.
- **Ollama Test (Conv 20a2d7f3)**: Provider failure -- all 3 messages got 404 errors, zero responses.
- **Dodecahedron + Primitives (Log only)**: Various geometry tests including dodecahedron (failed -- too complex for single script), hexagon sketch, sphere, box creation. Primitives succeeded, complex geometry failed.

### Key Findings
1. **Simple operations succeed reliably**: Box creation, sphere, extrusions, fillets, basic sketches
2. **Cut operations fail ~85% of the time**: "No target body found" is the most common error (12+ occurrences)
3. **Agent loses dimensional context after condensation**: Dimensions, positions, and spatial relationships not preserved
4. **Undo is completely broken**: 0/7 success rate
5. **Agent falsely claims success**: No verification gate catches failed operations
6. **Repetition detection fires but doesn't break error loops**: 15 warnings, no strategy changes
7. **Sketch coordinate confusion**: Face-local vs world-space coordinates consistently wrong
8. **`execute_script` variable scope**: Variables from one script not available in next (4+ occurrences)

### Session 2026-04-13 (v1.4.0 Implementation)
- All 15 v1.4.0 features implemented and tested
- All 10 open bugs (B003-B012) resolved
- 37 new unit tests added (492 total across 16 files)
- New module: `ai/design_state_tracker.py` for persistent design state
- Enhanced: `ai/claude_client.py` with pre-cut validation, post-op verification, screenshot budget, auto-diagnostics
- Enhanced: `ai/context_manager.py` with safe split points and design state preservation
- Enhanced: `ai/repetition_detector.py` with structured alternative suggestions
- Enhanced: `ai/providers/ollama_provider.py` with descriptive HTTP error messages
- Enhanced: `docs/F360_SKILL.md` with coordinate systems, profile selection, API signatures, scope isolation
- Fixed: `fusion/bridge.py` simulation responses now include `success: True`
- Fixed: `fusion_addin/addin_server.py` safe handling of unsaved documents
- Fixed: `.gitignore` now excludes `fusion_mcp.log`
- 0 test failures, 0 regressions

---

## Notes

- Fusion 360 API (`adsk.*`) is only available when running **inside** Fusion 360 as an add-in. The bridge module handles both modes gracefully.
- Claude API requires an Anthropic API key configured in the web UI settings panel or `config/config.json`.
- The web app runs at `localhost:8080` and communicates with the Fusion 360 add-in over a local bridge.
- Claude uses the Anthropic tool-use API for a full agent loop: reasoning, tool calls, observation, repeat.
- Multimodal support allows Claude to receive viewport screenshots as image content blocks.
