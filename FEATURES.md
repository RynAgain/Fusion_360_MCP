# Artifex360 -- Code Review Feature Tracker

> AI-powered design intelligence for Fusion 360 -- designs, manipulates, and operates Fusion 360 proficiently through Claude.

---

## Grumpy Reviewer's Summary

**Review conducted: 2026-04-18 | Reviewer: The one who has to read all this code**

Look. I just spent four days reading every file in this repository. All 1530 lines of `claude_client.py`. All 1600 lines of `addin_server.py`. The test suite that mocks everything so aggressively it is basically testing its own mocks. The frontend that injects raw HTML from an LLM into the DOM with zero sanitization and then has the audacity to call itself a "web application."

The v1.5.0 security pass (TASK-001 through TASK-045) fixed real problems. Credit where due. But it also left gaps you could drive a truck through -- the `exec()` sandbox still has `setattr` and `getattr` in its builtins (seriously?), there is zero CSRF protection on any Flask endpoint, and the frontend uses `marked.parse()` + `.innerHTML` like it is 2014 and XSS is something that happens to other people.

The architecture has two god classes, two parallel error classifiers, circular dependencies between the orchestration modules, and a test suite where the "thread safety test" literally just checks `hasattr(tracker, "_lock")` and calls it a day.

Here are 109 tasks. Every single one is a real problem I found in actual code. No "consider maybe possibly" suggestions. Fix them.

**Previous review status:** 45 tasks (TASK-001 through TASK-045) all implemented in v1.5.0/v1.6.0. Those are archived below.

---

## Status: All 109 Tasks Implemented -- 2026-04-18

All tasks from the code review (TASK-046 through TASK-154) have been implemented in a single session:

| Priority | Tasks | Status |
|----------|-------|--------|
| **P0 -- Critical** | TASK-046 to TASK-052 (7 tasks) | All complete |
| **P1 -- High** | TASK-053 to TASK-079 (27 tasks) | All complete |
| **P2 -- Medium** | TASK-080 to TASK-118 (39 tasks) | All complete |
| **P3 -- Low** | TASK-119 to TASK-154 (36 tasks) | All complete |

**Key changes:**
- 6 critical security fixes (exec sandbox, CSRF, XSS, thread safety, SSRF, auth)
- 27 high-priority bug fixes and security hardening
- 39 medium architecture and code quality improvements  
- 36 low-priority cleanups and test improvements
- 4 new test files created (test_rate_limiter, test_web_events, test_addin_server, test_settings)
- 3 new source files created (mcp/protocols.py, ai/orchestration_state.py, tests/)
- ~100+ new tests added across the suite

---

## v1.7.0 -- User Experience Improvements (2026-04-19)

Based on analysis of real design sessions (90-message enclosure build).

### [DONE] TASK-155: Web search result enhancement -- structured data extraction
- **Files:** `ai/web_search.py`
- **Problem:** `search_and_summarize()` returns raw text. When looking up product datasheets, the agent needs structured dimensions/specs.
- **Fix:** Enhance `fetch_page()` to detect product/spec pages and extract structured data (dimensions, mounting holes, pinouts).

### [DONE] TASK-156: Fusion API patterns in skill documentation
- **Files:** `docs/F360_SKILL.md`, `config/rules/fusion_design_iteration.md`
- **Problem:** Agent failed 3 consecutive times on extrude cuts because it didn't know `participantBodies` needs a Python list, not ObjectCollection. Cut direction from planes was also unclear.
- **Fix:** Add proven patterns for common failure modes to skill docs and design iteration rules.

### [DONE] TASK-157: Repetition detector sensitivity tuning
- **Files:** `ai/repetition_detector.py`
- **Problem:** The detector fires too aggressively for iterative scripting. Calling `execute_script` multiple times with different (but similar) arguments is normal workflow -- not a bug. The user can interrupt via stop button or new message.
- **Fix:** Raise thresholds, especially for `execute_script`. Distinguish "identical" calls (same args) from "similar" calls (different args, same tool).

### [DONE] TASK-158: Feature sequencing guidance in design rules
- **Files:** `config/rules/fusion_design_iteration.md`
- **Problem:** Fillets failed because snap clips created non-manifold edges. Operation ordering matters.
- **Fix:** Add operation ordering guidance: fillets/chamfers before detail features, boolean cuts after base geometry is complete.

### [DONE] TASK-159: Auto-cleanup empty conversation sessions
- **Files:** `ai/conversation_manager.py`
- **Problem:** 4 of 6 conversation files were empty (0 messages). Clutter in the conversation list.
- **Fix:** Don't persist conversations until first assistant response. Or clean up empties on list_all().

### [DONE] TASK-160: Document extraction pipeline (read_document tool)
- **Files:** `ai/document_extractor.py`, `mcp/server.py`, `mcp/tool_groups.py`, `requirements.txt`
- **Problem:** No way to read external files (PDFs, DOCX, images, text) from the agent. Users had to paste content manually.
- **Fix:** New `ai/document_extractor.py` module with PDF (PyMuPDF), DOCX, text, CSV, and image extraction. New `read_document` MCP tool with structured metadata (line counts, truncation info, image base64). 10 tests in `tests/test_document_extractor.py`.

---

## Severity Levels

- **P0** -- Active security vulnerability or data-loss risk. Stop what you are doing and fix this.
- **P1** -- High-severity security gap, critical bug, or blocking test gap. Fix this sprint.
- **P2** -- Architecture rot, medium bugs, or correctness issues. Fix soon.
- **P3** -- Code quality, cleanup, minor improvements. Fix when you have a spare afternoon.

---

## P0 -- CRITICAL / BLOCKING

### Security

#### [DONE] TASK-046: exec() sandbox escape via setattr/getattr/delattr/vars in safe builtins
- **Files:** [`fusion_addin/addin_server.py`](fusion_addin/addin_server.py:710)
- **Problem:** TASK-001 added a sandbox, but `setattr`, `delattr`, `getattr`, and `vars` are still in the safe builtins dict. Any of these can modify the sandbox namespace itself, re-introduce `__import__`, or walk the object graph to `os.system`. The sandbox is decorative.
- **Fix:** Remove `setattr`, `delattr`, `getattr`, `vars`, `type`, and `object` from safe builtins. Audit every remaining builtin against the CPython object graph. Better yet, use `RestrictedPython` or a subprocess jail.

#### [DONE] TASK-047: No CSRF protection on any Flask endpoint
- **Files:** [`web/app.py`](web/app.py)
- **Problem:** Zero CSRF tokens on any form or API endpoint. Any website the user visits can POST to `localhost:8080/api/settings`, change the API key, inject system prompts, or trigger agent actions. Combined with the Socket.IO CORS fix from TASK-009, this is the new easiest attack vector.
- **Fix:** Add `flask-wtf` CSRFProtect. For API routes, require a custom header (`X-Requested-With`) that browsers will not send cross-origin without CORS preflight.

#### [DONE] TASK-048: Stored XSS via marked.parse() + innerHTML -- no DOMPurify
- **Files:** [`web/static/js/app.js`](web/static/js/app.js:136)
- **Problem:** At least 6 call sites pipe LLM output through `marked.parse()` directly into `.innerHTML`. Claude's responses can contain arbitrary HTML. A malicious prompt injection or compromised model response achieves full DOM access -- steal cookies, exfiltrate conversation history, modify the UI to phish credentials. This is not theoretical.
- **Fix:** Add DOMPurify. `element.innerHTML = DOMPurify.sanitize(marked.parse(text))` at every injection point. Or use `marked` with a renderer that escapes HTML.

#### [DONE] TASK-049: Thread-safety race on conversation_history mutations outside lock
- **Files:** [`ai/claude_client.py`](ai/claude_client.py:930)
- **Problem:** TASK-012 added a turn mutex, which is good. But [`_run_turn_inner()`](ai/claude_client.py:930) copies `conversation_history` under `_lock` (line 930-932) then mutates the copy outside it (line 948+). Meanwhile [`set_conversation()`](ai/claude_client.py:1454) can overwrite the entire history mid-turn (line 1454-1456), and [`_run_turn_inner`](ai/claude_client.py:1050) appends to the live list at line 1050. Classic copy-then-mutate-original race.
- **Fix:** Hold the lock for all mutations to `conversation_history` within a turn, or use a copy-on-write snapshot that is atomically swapped back.

#### [DONE] TASK-050: SO_REUSEADDR + world-readable token file = local privilege escalation
- **Files:** [`fusion_addin/addin_server.py`](fusion_addin/addin_server.py)
- **Problem:** TASK-002 added token auth. But the TCP server sets `SO_REUSEADDR`, meaning another process can bind the same port and intercept connections. The token file has default filesystem permissions (world-readable on most setups). Any local process can read the token, race to bind the port, and impersonate the Fusion 360 add-in.
- **Fix:** Remove `SO_REUSEADDR` (or use `SO_REUSEPORT` only on platforms that support it safely). Set token file permissions to owner-only (`0600`). Consider using a named pipe or Unix domain socket instead of TCP.

#### [DONE] TASK-051: sanitize() and SecretFilter in log_sanitizer.py have zero test coverage
- **Files:** [`ai/log_sanitizer.py`](ai/log_sanitizer.py), [`tests/test_log_sanitizer_compact.py`](tests/test_log_sanitizer_compact.py)
- **Problem:** The security-critical code that strips API keys, tokens, and secrets from logs and saved conversations has exactly zero test coverage. The existing test file tests something else entirely. If the regex patterns are wrong (and H-10 suggests they miss non-standard formats), we would never know.
- **Fix:** Write comprehensive tests: API keys in various positions, partial matches, base64-encoded secrets, multi-line content, edge cases. This is security code -- it needs 100% branch coverage.

### Bugs

#### [DONE] TASK-052: No maximum iteration guard on agentic while True loop
- **Files:** [`ai/claude_client.py`](ai/claude_client.py:936)
- **Problem:** The agentic tool loop is a `while True` with no iteration cap. A model that keeps returning `tool_use` blocks (or a prompt injection that induces one) will loop indefinitely, consuming tokens until the API key is drained or the heat death of the universe, whichever comes first.
- **Fix:** Add a configurable max iteration count (default: 50). After the limit, inject a forced stop message and return what we have. Log a warning.

---

## P1 -- HIGH SEVERITY

### Security

#### [DONE] TASK-053: No authentication on any web API route
- **Files:** [`web/routes.py`](web/routes.py)
- **Problem:** Every endpoint under `/api/` is wide open. Anyone on the network (if not bound to localhost) can read settings, load conversations, change the AI provider, trigger agent actions. TASK-002 added TCP auth for the Fusion bridge but the web layer has nothing.
- **Fix:** At minimum, require a session token or API key for all routes. For local-only use, validate `Origin` header.

#### [DONE] TASK-054: Settings injection -- /api/settings POST accepts arbitrary keys
- **Files:** [`config/settings.py`](config/settings.py), [`web/routes.py`](web/routes.py)
- **Problem:** The settings update endpoint accepts any JSON keys and merges them into the settings dict. An attacker can inject arbitrary configuration: change the model, modify system prompt paths, alter provider URLs to point at a malicious server, or set internal flags.
- **Fix:** Allowlist of settable keys. Reject anything not on the list.

#### [DONE] TASK-055: Information disclosure -- Python tracebacks to client via Socket.IO
- **Files:** [`web/events.py`](web/events.py:324)
- **Problem:** Multiple Socket.IO handlers emit raw Python tracebacks to the browser on error. This leaks internal paths, dependency versions, and potentially secrets from local variables in the stack frames.
- **Fix:** Log the full traceback server-side. Send a generic error message to the client. Never send `traceback.format_exc()` over the wire.

#### [DONE] TASK-056: SSRF in web_search.fetch_page() -- no internal network protection
- **Files:** [`ai/web_search.py`](ai/web_search.py)
- **Problem:** `fetch_page()` fetches any URL the AI agent requests, including `http://169.254.169.254/latest/meta-data/` (AWS metadata), `http://localhost:9876/` (the Fusion bridge), or any internal service. Classic SSRF.
- **Fix:** Block private/reserved IP ranges (10.x, 172.16-31.x, 192.168.x, 169.254.x, 127.x, ::1). Resolve DNS before connecting and check the resolved IP.

#### [DONE] TASK-057: Path traversal bypass on Windows -- case-insensitive check
- **Files:** [`fusion/bridge.py`](fusion/bridge.py:458)
- **Problem:** The path traversal fix from TASK-007 uses string comparison, but Windows paths are case-insensitive. `C:\Users\..\..\..\Windows\System32` with mixed case can bypass the check.
- **Fix:** Use `os.path.realpath()` and compare normalized paths. On Windows, compare with `os.path.normcase()`.

#### [DONE] TASK-058: Path traversal in conversation load/delete via routes.py
- **Files:** [`web/routes.py`](web/routes.py:211)
- **Problem:** TASK-005 fixed `conversation_manager.py`, but [`routes.py`](web/routes.py:211) passes the raw `conversation_id` from the URL directly to the manager. If the validation in the manager is bypassed or insufficient, the route layer has no defense.
- **Fix:** Validate `conversation_id` matches UUID pattern in the route handler before passing to the manager. Defense in depth.

#### [DONE] TASK-059: Git argument injection via unsanitized design_name in branch names
- **Files:** [`ai/git_design_manager.py`](ai/git_design_manager.py)
- **Problem:** [`_git()`](ai/git_design_manager.py) passes `design_name` into git commands as branch names without validation. A name like `--upload-pack=malicious` or one containing shell metacharacters can inject git arguments.
- **Fix:** Validate `design_name` against `^[a-zA-Z0-9_-]+$`. Use `--` separator before branch name arguments.

#### [DONE] TASK-060: Inline onclick XSS vectors in app.js
- **Files:** [`web/static/js/app.js`](web/static/js/app.js)
- **Problem:** Document list, mode selector, and conversations list build HTML with inline `onclick` handlers using string interpolation. If any dynamic value contains a quote character, it breaks out of the attribute and enables XSS.
- **Fix:** Use `addEventListener` instead of inline handlers. Or at minimum, properly escape all interpolated values.

### Bugs

#### [DONE] TASK-061: checkpoint_manager.get() and get_latest() lie about return type
- **Files:** [`ai/checkpoint_manager.py`](ai/checkpoint_manager.py)
- **Problem:** Type annotations say these return `DesignCheckpoint` but they can return `None`. Every caller that trusts the type hint and does not null-check will crash with `AttributeError` on an empty checkpoint list.
- **Fix:** Update return type to `Optional[DesignCheckpoint]`. Audit all call sites.

#### [DONE] TASK-062: _force_stop double-assignment terminates legitimate tool calls
- **Files:** [`ai/claude_client.py`](ai/claude_client.py)
- **Problem:** The `_force_stop` flag is set in one code path and then a subsequent legitimate tool call check reads it before it is cleared, causing the tool call to be incorrectly terminated.
- **Fix:** Clear `_force_stop` at the beginning of each turn iteration, not at the end.

#### [DONE] TASK-063: TOCTOU race in _ExecuteEventHandler.notify()
- **Files:** [`fusion_addin/addin_server.py`](fusion_addin/addin_server.py:288)
- **Problem:** The event handler checks a condition and then acts on it non-atomically. Between the check and the action, another thread can change the state.
- **Fix:** Use a lock or atomic operation to make the check-and-act sequence indivisible.

#### [DONE] TASK-064: Bare except Exception leaves result_q unbound
- **Files:** [`fusion_addin/addin_server.py`](fusion_addin/addin_server.py:296)
- **Problem:** The except block swallows the cause and leaves `result_q` potentially unbound. The caller blocks forever waiting on a queue that never gets a result.
- **Fix:** Always put an error result on the queue in the except block. Re-raise or log the original exception.

#### [DONE] TASK-065: execute_script timeout parameter accepted but never enforced
- **Files:** [`fusion_addin/addin_server.py`](fusion_addin/addin_server.py:651)
- **Problem:** The `timeout` parameter is accepted in the API but the `exec()` call has no timeout mechanism. A script with an infinite loop blocks the Fusion 360 UI thread forever.
- **Fix:** Run script in a separate thread with `threading.Timer` for the timeout. Or document that timeout is not supported and remove the parameter (lying APIs are worse than limited APIs).

#### [DONE] TASK-066: FusionBridge._authenticate has no timeout on read loop
- **Files:** [`fusion/bridge.py`](fusion/bridge.py:178)
- **Problem:** The authentication handshake reads from the socket in a loop with no timeout. If the server never responds (or responds partially), the bridge hangs forever.
- **Fix:** Set `socket.settimeout()` before the auth read loop. Raise `ConnectionError` on timeout.

#### [DONE] TASK-067: FusionBridge._send_command holds lock for entire send+recv -- potential deadlock
- **Files:** [`fusion/bridge.py`](fusion/bridge.py:270)
- **Problem:** The lock is held from send through recv. If recv blocks (server is slow or crashed), no other thread can even check `connected` status or attempt cleanup. Deadlock if any other code path tries to acquire the same lock.
- **Fix:** Use a per-request lock or release the send lock before recv and use a response correlation mechanism.

#### [DONE] TASK-068: _send silently swallows all send errors
- **Files:** [`fusion_addin/addin_server.py`](fusion_addin/addin_server.py:252)
- **Problem:** All exceptions in `_send()` are caught and silently dropped. If a response fails to send, the client hangs waiting for a response that was eaten.
- **Fix:** Log the error. Close the connection so the client gets a clean disconnect rather than hanging.

#### [DONE] TASK-069: SubtaskManager and ContextBridge access private attributes directly
- **Files:** [`ai/subtask_manager.py`](ai/subtask_manager.py), [`ai/context_bridge.py`](ai/context_bridge.py)
- **Problem:** `SubtaskManager` reads `client.conversation_history`, `client._system_prompt`, and `client.mode_manager._active_mode` directly. `ContextBridge` reads `task_manager._tasks` and `task_manager._plan_title`. This is not encapsulation -- it is hope-driven development. Any refactor of `ClaudeClient` or `TaskManager` internals silently breaks these modules.
- **Fix:** Add public accessor methods: `client.get_conversation_snapshot()`, `client.get_system_prompt()`, `client.get_active_mode()`, `task_manager.get_tasks()`, `task_manager.get_plan_title()`.

#### [DONE] TASK-070: Provider switch failure silently swallowed
- **Files:** [`web/routes.py`](web/routes.py:96)
- **Problem:** If switching providers fails, the error is caught and the route returns success anyway. The user thinks they switched to Ollama but they are still on Anthropic.
- **Fix:** Return the actual error. If the switch fails, do not pretend it succeeded.

#### [DONE] TASK-071: _get_body_list uses Application.get() instead of self._app
- **Files:** [`fusion_addin/addin_server.py`](fusion_addin/addin_server.py:582)
- **Problem:** This method calls `Application.get()` (a static/class method) instead of using the instance's `self._app` reference. If the application singleton ever differs from the instance reference, this silently operates on the wrong application context.
- **Fix:** Use `self._app` consistently.

#### [DONE] TASK-072: _create_cylinder ternary with fragile operator precedence
- **Files:** [`fusion_addin/addin_server.py`](fusion_addin/addin_server.py:439)
- **Problem:** A ternary expression with arithmetic has ambiguous precedence. Without explicit parentheses, one wrong mental model of Python's precedence rules means the wrong value gets used for the cylinder dimension.
- **Fix:** Add explicit parentheses. Code that requires the reader to recall operator precedence tables is a bug waiting to happen.

### Testing

#### [DONE] TASK-073: Socket.IO event handlers have zero test coverage
- **Files:** [`web/events.py`](web/events.py), `tests/`
- **Problem:** Every Socket.IO event handler (user_message, cancel, tool_confirmation, settings updates) is completely untested. This is the primary user-facing interface of the application.
- **Fix:** Add tests using `flask-socketio` test client. Cover: message handling, cancellation, error cases, concurrent connections.

#### [DONE] TASK-074: ai/rate_limiter.py has zero test coverage
- **Files:** [`ai/rate_limiter.py`](ai/rate_limiter.py), `tests/`
- **Problem:** Zero tests for the rate limiter. It uses `time.sleep()` busy-waiting (see TASK-085) but we cannot even verify its basic functionality.
- **Fix:** Write tests covering: token counting, wait behavior, concurrent access, reset behavior.

#### [DONE] TASK-075: test_fusion_bridge.py only tests "not connected" error path
- **Files:** [`tests/test_fusion_bridge.py`](tests/test_fusion_bridge.py)
- **Problem:** Every test asserts that the bridge returns an error when not connected. Zero tests for actual command execution, authentication, reconnection, or simulation mode behavior.
- **Fix:** Add tests with mocked socket for: connect, authenticate, send command, receive response, timeout, reconnect.

#### [DONE] TASK-076: test_design_state_tracker.py "thread safety test" is dead code
- **Files:** [`tests/test_design_state_tracker.py`](tests/test_design_state_tracker.py)
- **Problem:** The "thread safety" test only checks `hasattr(tracker, "_lock")`. That is not a thread safety test. That is checking if an attribute exists. A dictionary has attributes too; that does not make it thread-safe.
- **Fix:** Write an actual concurrency test: spawn threads, have them call `update()` simultaneously, verify no data corruption.

#### [DONE] TASK-077: No mid-turn cancellation test in test_agent_loop.py
- **Files:** [`tests/test_agent_loop.py`](tests/test_agent_loop.py)
- **Problem:** TASK-015 implemented cancellation, but there is no test verifying that setting the cancel event actually stops a multi-tool turn mid-execution.
- **Fix:** Add test that starts a turn, sets cancel event after first tool call, verifies the loop exits cleanly.

#### [DONE] TASK-078: fusion_addin/addin_server.py is completely untested
- **Files:** [`fusion_addin/addin_server.py`](fusion_addin/addin_server.py), `tests/`
- **Problem:** 1600 lines of code. Zero tests. This is the code that directly controls Fusion 360 -- the entire point of the application. Not a single handler, parser, or validator is tested.
- **Fix:** Start with the pure-logic functions (body lookup, parameter parsing, response formatting). Mock `adsk` for handler tests. This is a multi-PR effort but it needs to start.

#### [DONE] TASK-079: test_error_classifier.py mock over-scoping
- **Files:** [`tests/test_error_classifier.py`](tests/test_error_classifier.py)
- **Problem:** `settings.get.return_value = True` mocks ALL settings keys to return `True`. This means tests pass even if the code checks the wrong setting key. The mock is too broad to catch real bugs.
- **Fix:** Use `side_effect` with a dict to return different values per key.

---

## P2 -- MEDIUM / ARCHITECTURE

### Architecture

#### [DONE] TASK-080: ClaudeClient is a ~1530-line god class with ~30 public methods
- **Files:** [`ai/claude_client.py`](ai/claude_client.py)
- **Problem:** This single class owns: conversation state, token tracking, screenshot budgeting, rate limiting, context management, repetition detection, mode management, task management, checkpoint management, design state tracking, orchestration, subtask management, provider management, event emission, and the agentic tool loop. It is 1530 lines of interleaved responsibilities. Every new feature bolts onto this class because everything already depends on it.
- **Fix:** Extract into focused modules: `AgentLoop` (the while-true tool loop), `TurnState` (per-turn conversation snapshot), `TokenTracker`, `ScreenshotBudget`. ClaudeClient becomes a thin coordinator.

#### [DONE] TASK-081: fusion_addin/addin_server.py is also a god class (~1600 lines)
- **Files:** [`fusion_addin/addin_server.py`](fusion_addin/addin_server.py)
- **Problem:** TCP server, authentication, request routing, response formatting, and every single Fusion 360 operation handler -- all in one file. Adding a new tool means scrolling past 1600 lines of unrelated handlers.
- **Fix:** Extract handlers into per-domain modules: `geometry_handlers.py`, `sketch_handlers.py`, `document_handlers.py`, `export_handlers.py`. Keep `addin_server.py` as the TCP server + router.

#### [DONE] TASK-082: No Protocol/ABC for MCP server interface
- **Files:** [`mcp/server.py`](mcp/server.py)
- **Problem:** There is no abstract base class or Protocol defining what an MCP server must implement. The bridge, addin, and test code all assume specific method signatures with no contract enforcement.
- **Fix:** Define `MCPServerProtocol` with `typing.Protocol` or `abc.ABC`. Type-check against it.

#### [DONE] TASK-083: ContextBridge / SubtaskManager circular conceptual dependency
- **Files:** [`ai/context_bridge.py`](ai/context_bridge.py), [`ai/subtask_manager.py`](ai/subtask_manager.py)
- **Problem:** ContextBridge needs SubtaskManager's results to build context. SubtaskManager needs ContextBridge to provide context for subtasks. This circular dependency is currently "solved" by both reaching into ClaudeClient's internals (see TASK-069).
- **Fix:** Introduce a shared `OrchestrationState` dataclass that both modules read from and write to, breaking the circular dependency.

#### [DONE] TASK-084: Two parallel error classification systems
- **Files:** [`ai/error_classifier.py`](ai/error_classifier.py), [`ai/system_prompt.py`](ai/system_prompt.py)
- **Problem:** `error_classifier.py` has regex-based pattern matching for 7 error types. The system prompt has a separate prompt-based error policy (from the autoresearch integration). Both classify errors independently with potentially conflicting results. The regex patterns overlap and can misclassify.
- **Fix:** Pick one system. The prompt-based policy is more flexible. Demote the regex classifier to a "hint" that feeds into the prompt, not a parallel decision path.

#### [DONE] TASK-085: No retry logic for LLM API calls
- **Files:** [`ai/providers/anthropic_provider.py`](ai/providers/anthropic_provider.py), [`ai/providers/ollama_provider.py`](ai/providers/ollama_provider.py)
- **Problem:** A single transient 500 or timeout from the API provider fails the entire turn. No exponential backoff, no retry. In a tool that takes 5 minutes of agent work per turn, this is unacceptable.
- **Fix:** Add retry with exponential backoff (3 attempts, 1s/2s/4s) for 429, 500, 502, 503, and timeout errors. Use `tenacity` or a simple decorator.

#### [DONE] TASK-086: Condensation uses same LLM provider -- poor quality with small Ollama models
- **Files:** [`ai/context_manager.py`](ai/context_manager.py)
- **Problem:** Context condensation uses whatever provider is active. If the user is running a 7B Ollama model, condensation summaries are terrible, losing critical design dimensions and spatial relationships.
- **Fix:** Allow configuring a separate "summarization provider" (e.g., always use Anthropic for condensation even when the main provider is Ollama). Or fall back to rule-based condensation for small models.

#### [DONE] TASK-087: TOOL_DEFINITIONS is a 750-line hand-maintained list with no cross-validation
- **Files:** [`mcp/tool_groups.py`](mcp/tool_groups.py), [`mcp/server.py`](mcp/server.py), [`fusion/bridge.py`](fusion/bridge.py), [`fusion_addin/addin_server.py`](fusion_addin/addin_server.py), [`web/static/js/app.js`](web/static/js/app.js)
- **Problem:** Tool definitions exist in up to 5 locations with no validation that they are consistent. Adding a tool means updating multiple files and hoping you got the parameter names right in all of them.
- **Fix:** Single source of truth for tool definitions (JSON schema or Python dataclass). Generate everything else from it. Add a CI check that validates consistency.

### Bugs

#### [DONE] TASK-088: checkpoint_manager.restore() -- ValueError + in-place caller mutation
- **Files:** [`ai/checkpoint_manager.py`](ai/checkpoint_manager.py)
- **Problem:** Two issues: (1) `_checkpoints.index()` raises `ValueError` if the checkpoint was removed concurrently. (2) `restore()` mutates the caller's messages list in-place via slice assignment, which is a side-effect surprise for the caller.
- **Fix:** (1) Use a try/except or dict lookup. (2) Return a new list instead of mutating in-place.

#### [DONE] TASK-089: rate_limiter.acquire() busy-waits with time.sleep(0.1)
- **Files:** [`ai/rate_limiter.py`](ai/rate_limiter.py)
- **Problem:** The rate limiter spins in a loop calling `time.sleep(0.1)` waiting for the rate window to pass. This wastes CPU and has 100ms jitter.
- **Fix:** Use `threading.Condition` with `wait(timeout)` for precise, CPU-efficient waiting.

#### [DONE] TASK-090: DesignStateTracker.update() makes N+1 network calls
- **Files:** [`ai/design_state_tracker.py`](ai/design_state_tracker.py)
- **Problem:** `update()` calls `get_body_properties` separately for every body in the design. For a design with 20 bodies, that is 20 round-trips to the Fusion 360 add-in over TCP.
- **Fix:** Add a batch endpoint `get_all_body_properties` that returns properties for all bodies in one call.

#### [DONE] TASK-091: conversation_manager.save() double-serialization
- **Files:** [`ai/conversation_manager.py`](ai/conversation_manager.py)
- **Problem:** `save()` calls `json.dumps()` then `json.loads()` on the conversation data. For a conversation with 200 messages and embedded base64 screenshots, this is a lot of wasted CPU and memory for... nothing.
- **Fix:** Remove the round-trip. If the goal is deep-copy, use `copy.deepcopy()`. If the goal is validation, validate before serializing.

#### [DONE] TASK-092: conversation_manager.list_all() loads entire JSON files for metadata
- **Files:** [`ai/conversation_manager.py`](ai/conversation_manager.py)
- **Problem:** To list conversations (showing just ID, title, date), every JSON file is fully loaded and parsed. A directory with 100 saved conversations means loading potentially hundreds of MB of message data just to show a list.
- **Fix:** Store metadata in a separate index file, or read only the first N bytes of each file to extract the header.

#### [DONE] TASK-093: design_state_tracker.update() bare except Exception: pass
- **Files:** [`ai/design_state_tracker.py`](ai/design_state_tracker.py)
- **Problem:** TASK-025 was supposed to fix bare excepts in this file, but the `update()` method still swallows all exceptions silently. If the design state tracker fails, nobody knows, and the agent operates with stale state.
- **Fix:** `logger.exception("Failed to update design state")`. Return a partial result rather than silently returning stale data.

#### [DONE] TASK-094: git_design_manager robustness issues
- **Files:** [`ai/git_design_manager.py`](ai/git_design_manager.py)
- **Problem:** Two issues: (1) `reject_iteration()` runs `git reset --hard HEAD~1` which fails with "fatal: ambiguous argument" when there is only one commit. (2) `_append_iteration_log()` is not atomic -- a crash mid-write corrupts the TSV log.
- **Fix:** (1) Check commit count before reset; on single commit, use `git update-ref -d HEAD`. (2) Write to temp file then `os.rename()` for atomic replacement.

#### [DONE] TASK-095: rules_loader._parse_yaml_frontmatter() is a hand-rolled YAML parser
- **Files:** [`ai/rules_loader.py`](ai/rules_loader.py)
- **Problem:** The frontmatter parser is a regex-based approximation that cannot handle: colons in values, multi-line strings, lists, nested objects, or any YAML feature beyond `key: simple_value`. This will silently produce wrong results as rule files get more complex.
- **Fix:** Use `pyyaml` (`yaml.safe_load`). It is already a transitive dependency.

#### [DONE] TASK-096: subtask_manager.execute_subtask() potential deadlock
- **Files:** [`ai/subtask_manager.py`](ai/subtask_manager.py)
- **Problem:** If `execute_subtask()` is called from inside a turn that already holds `_turn_lock`, it will attempt to acquire the lock again (for the subtask's turn), causing a deadlock.
- **Fix:** Use `threading.RLock` (reentrant lock) or restructure so subtask execution never nests inside a locked turn.

#### [DONE] TASK-097: task_manager.create_orchestrated_plan() does not validate dependency indices
- **Files:** [`ai/task_manager.py`](ai/task_manager.py)
- **Problem:** Dependency indices are accepted without validation. An index pointing to a non-existent step, a self-dependency, or a circular dependency chain will cause the orchestrator to silently block forever waiting for a step that can never complete.
- **Fix:** Validate: indices are in range, no self-dependencies, no cycles (topological sort). Reject invalid plans at creation time.

#### [DONE] TASK-098: web_search.fetch_page() does not limit redirects
- **Files:** [`ai/web_search.py`](ai/web_search.py)
- **Problem:** No redirect limit means the agent can be bounced through an arbitrary number of redirects, potentially to internal addresses (compounding TASK-056) or into an infinite redirect loop.
- **Fix:** Set `max_redirects=5` on the requests session.

#### [DONE] TASK-099: anthropic_provider max_tokens clamping too aggressive
- **Files:** [`ai/providers/anthropic_provider.py`](ai/providers/anthropic_provider.py)
- **Problem:** Output tokens are capped at 40K even for models that support 128K output. This artificially limits the model's ability to generate long responses (complex scripts, detailed plans).
- **Fix:** Clamp to the model's actual `max_output_tokens` from the model registry, not a hardcoded 40K.

#### [DONE] TASK-100: ollama_provider._convert_messages() drops images for vision-capable models
- **Files:** [`ai/providers/ollama_provider.py`](ai/providers/ollama_provider.py)
- **Problem:** Image content blocks are dropped for ALL Ollama models during message conversion, even models that support vision (llava, bakllava, etc.). The model discovery already detects vision capability -- it is just not used during conversion.
- **Fix:** Check the model's vision capability flag. If vision-capable, include images in the converted messages.

#### [DONE] TASK-101: ClaudeClient.clear_history() and new_conversation() are near-duplicates
- **Files:** [`ai/claude_client.py`](ai/claude_client.py)
- **Problem:** Two methods that do almost the same thing (reset conversation state) with slight differences. Callers have to guess which one to use, and bugs fixed in one are not fixed in the other.
- **Fix:** Make one call the other, or extract the shared logic into a private `_reset_state()` method.

#### [DONE] TASK-102: Single global threading.Event for cancellation shared across all clients
- **Files:** [`web/events.py`](web/events.py:27)
- **Problem:** One `_cancel_event` for all Socket.IO clients. If user A cancels, user B's in-flight request is also cancelled. There is also a TOCTOU race on `_cancel_event.clear()` (lines 303-306) where a cancel can be lost between check and clear.
- **Fix:** Per-session cancel events. Use `Event.clear()` atomically at the start of each turn.

#### [DONE] TASK-103: No message length validation on user input
- **Files:** [`web/events.py`](web/events.py:64)
- **Problem:** User messages are accepted at any length and passed directly to the LLM. A 10MB message will blow up token counting, context management, and the API call.
- **Fix:** Reject messages over a configurable limit (e.g., 100KB). Return an error to the client.

#### [DONE] TASK-104: Settings save race condition -- no file locking
- **Files:** [`config/settings.py`](config/settings.py:102)
- **Problem:** Two concurrent settings saves (e.g., from settings UI and provider switch) can interleave their file writes, producing corrupt JSON.
- **Fix:** Use `fcntl.flock` (Unix) / `msvcrt.locking` (Windows) or write-to-temp-then-rename pattern.

#### [DONE] TASK-105: _find_body partial match is too loose
- **Files:** [`fusion_addin/addin_server.py`](fusion_addin/addin_server.py:879)
- **Problem:** Body lookup uses substring matching. Searching for "Box" matches "Box", "Box (1)", "Toolbox", and "BoxShadow". The first match wins, which is often not the intended body.
- **Fix:** Try exact match first. Fall back to prefix match. If multiple matches, return all candidates with metadata so the caller can disambiguate.

#### [DONE] TASK-106: _create_sphere silently ignores conflicting diameter/radius parameters
- **Files:** [`fusion_addin/addin_server.py`](fusion_addin/addin_server.py)
- **Problem:** If both `diameter` and `radius` are provided with conflicting values, one silently wins. The agent does not know which was used.
- **Fix:** If both are provided and conflict, return an error. Do not guess.

#### [DONE] TASK-107: Module-level mutable globals undermine app factory pattern
- **Files:** [`web/app.py`](web/app.py:47)
- **Problem:** TASK-036 was supposed to fix this. But mutable globals at module scope (lines 47-50) still exist and prevent proper app factory usage, making testing with multiple app instances impossible.
- **Fix:** Move all mutable state into `app.extensions` or `app.config`. The `create_app()` factory should be the only way to create state.

#### [DONE] TASK-108: execute_tool logs full tool input including scripts and base64 images
- **Files:** [`fusion_addin/addin_server.py`](fusion_addin/addin_server.py)
- **Problem:** Every tool invocation is logged with its full input, including multi-KB Python scripts and base64-encoded screenshots. This bloats logs to megabytes and can leak sensitive content.
- **Fix:** Truncate logged input to a reasonable length (e.g., 500 chars). Redact base64 content entirely.

#### [DONE] TASK-109: No input validation in MCPServer.execute_tool
- **Files:** [`mcp/server.py`](mcp/server.py)
- **Problem:** Tool inputs are passed through to handlers with no schema validation. Missing required parameters, wrong types, and extra parameters all reach handler code that may not handle them gracefully.
- **Fix:** Validate tool inputs against their JSON schema definitions before dispatching.

#### [DONE] TASK-110: FusionBridge.execute() rebuilds dispatch dict every call
- **Files:** [`fusion/bridge.py`](fusion/bridge.py:592)
- **Problem:** The command-to-handler dispatch dictionary is rebuilt on every `execute()` call. This is a dict literal with ~40 entries constructed and discarded on every single tool invocation.
- **Fix:** Build the dispatch dict once in `__init__` or as a class attribute.

#### [DONE] TASK-111: _handle_close_document double-save
- **Files:** [`fusion_addin/addin_server.py`](fusion_addin/addin_server.py)
- **Problem:** The close document handler saves the document twice before closing. If the first save succeeds, the second is redundant. If the first fails, the second probably will too.
- **Fix:** Save once.

#### [DONE] TASK-112: STL export creates options object twice
- **Files:** [`fusion_addin/addin_server.py`](fusion_addin/addin_server.py)
- **Problem:** STL export options are created, configured, then created again and re-configured. The first creation is wasted work.
- **Fix:** Create once, configure once.

#### [DONE] TASK-113: connected property vs _send_command lock contention
- **Files:** [`fusion/bridge.py`](fusion/bridge.py:127)
- **Problem:** The `connected` property reads socket state outside the lock, while `_send_command` holds the lock for its entire duration (see TASK-067). This creates a race where `connected` returns True but by the time `_send_command` runs, the connection is gone.
- **Fix:** Make `connected` acquire the lock (briefly). Or accept that it is a hint and handle disconnection in `_send_command`.

### Testing

#### [DONE] TASK-114: time.sleep() used for thread synchronization in tests -- flaky CI
- **Files:** Multiple test files in [`tests/`](tests/)
- **Problem:** Thread synchronization tests use `time.sleep()` with hardcoded delays. These pass on fast developer machines and flake on slow CI runners.
- **Fix:** Use `threading.Event`, `threading.Barrier`, or `unittest.mock.patch('time.sleep')`. Never rely on wall-clock timing.

#### [DONE] TASK-115: No tests for corrupt JSON loading in conversation_manager
- **Files:** [`tests/test_conversation_manager.py`](tests/test_conversation_manager.py)
- **Problem:** What happens when a conversation JSON file is truncated, contains invalid UTF-8, or has a schema mismatch? Nobody knows because there are no tests for it.
- **Fix:** Add tests with corrupt/truncated/malformed JSON files. Verify graceful error handling.

#### [DONE] TASK-116: test_system_prompt.py skips when file not found -- may never run in CI
- **Files:** [`tests/test_system_prompt.py`](tests/test_system_prompt.py)
- **Problem:** Tests are skipped with `unittest.skipUnless(os.path.exists(...))`. If the CI environment does not have the expected file layout, the entire test file is silently skipped. Zero coverage, zero failures -- the worst kind of test.
- **Fix:** Use fixtures or mock file reads. Tests should not depend on filesystem state.

#### [DONE] TASK-117: No tests for POST endpoints with malformed JSON
- **Files:** [`tests/test_web_routes.py`](tests/test_web_routes.py)
- **Problem:** All POST tests send well-formed JSON. What happens with: empty body, non-JSON content-type, truncated JSON, missing required fields? If the handlers do not validate (see TASK-109), these will crash with 500 errors.
- **Fix:** Add negative test cases for every POST endpoint.

#### [DONE] TASK-118: config/settings.py has no dedicated test file
- **Files:** [`config/settings.py`](config/settings.py), `tests/`
- **Problem:** The settings module handles persistence, encoding, validation, and defaults. It has no dedicated test file. Coverage comes incidentally from other tests, if at all.
- **Fix:** Create `tests/test_settings.py` covering: load, save, update, encoding, validation, defaults, concurrent access.

---

## P3 -- LOW / CODE QUALITY

### Code Quality

#### [DONE] TASK-119: Missing `__all__` exports across modules
- **Files:** Multiple `__init__.py` files
- **Problem:** No module defines `__all__`, making it unclear what the public API is. `from ai import *` grabs everything, including internal helpers.
- **Fix:** Add `__all__` to every public module. Start with `ai/__init__.py`, `mcp/__init__.py`, `web/__init__.py`.

#### [DONE] TASK-120: DesignCheckpoint is not a dataclass
- **Files:** [`ai/checkpoint_manager.py`](ai/checkpoint_manager.py)
- **Problem:** `DesignCheckpoint` is a plain class with manual `__init__`, no `__repr__`, no `__eq__`, no slot optimization. It is a data container that does not know it is a data container.
- **Fix:** Convert to `@dataclass` (or `@dataclass(frozen=True)` if immutability is desired).

#### [DONE] TASK-121: EventType is not an Enum
- **Files:** [`ai/claude_client.py`](ai/claude_client.py)
- **Problem:** Event types are string constants scattered through the codebase. Typos in event names fail silently.
- **Fix:** Define `class EventType(str, Enum)` with all event names. Use it everywhere events are emitted or handled.

#### [DONE] TASK-122: system_prompt.py import-time side effects
- **Files:** [`ai/system_prompt.py`](ai/system_prompt.py)
- **Problem:** `from config.settings import settings` at module level triggers settings loading (file I/O, JSON parsing) as a side effect of importing the module. This makes the module untestable without mocking at import time.
- **Fix:** Accept settings as a parameter or use lazy import inside functions.

#### [DONE] TASK-123: repetition_detector._hash_args() uses MD5
- **Files:** [`ai/repetition_detector.py`](ai/repetition_detector.py)
- **Problem:** MD5 is used for hashing tool arguments. While not a security issue here (it is for deduplication), it is a code smell that triggers security scanners and fails FIPS compliance checks.
- **Fix:** Use `hashlib.sha256` or even `hash()`. Same API, no scanner noise.

#### [DONE] TASK-124: Magic numbers in context_manager.py not configurable
- **Files:** [`ai/context_manager.py`](ai/context_manager.py)
- **Problem:** Token thresholds, condensation ratios, and buffer sizes are hardcoded magic numbers throughout the file. Tuning these requires code changes.
- **Fix:** Move to `config/settings.py` with sane defaults. Allow override via settings UI.

#### [DONE] TASK-125: context_manager._llm_summarize() Demeter violation -- 4-level duck-typing chain
- **Files:** [`ai/context_manager.py`](ai/context_manager.py)
- **Problem:** The summarization code reaches through `client.provider_manager.active.create_message(...)` -- four levels of object traversal. If any intermediate object changes shape, this breaks silently.
- **Fix:** Add a `client.summarize(messages)` method that encapsulates the provider chain.

#### [DONE] TASK-126: modes.py captures TOOL_GROUPS.keys() at import time -- stale after registration
- **Files:** [`ai/modes.py`](ai/modes.py)
- **Problem:** TASK-039 was supposed to fix this, but the "full" mode still captures tool groups at import time. Any tool group registered after import (plugins, dynamic registration) is excluded.
- **Fix:** Make "full" mode dynamically query `TOOL_GROUPS.keys()` at mode resolution time, not import time.

#### [DONE] TASK-127: ollama_provider.stream_message() catches overly broad exceptions
- **Files:** [`ai/providers/ollama_provider.py`](ai/providers/ollama_provider.py)
- **Problem:** TASK-044 was supposed to narrow exception catches, but `stream_message()` still catches `OSError` which includes file system errors, permission errors, and other non-network issues.
- **Fix:** Catch only `requests.RequestException` and `ConnectionError`.

#### [DONE] TASK-128: f-string logging defeats lazy evaluation
- **Files:** Multiple files across `ai/`, `web/`, `fusion/`
- **Problem:** Pervasive use of `logger.debug(f"message {expensive_expr}")` evaluates the f-string even when debug logging is disabled. In hot paths (every tool call, every message), this adds measurable overhead.
- **Fix:** Use `logger.debug("message %s", expensive_expr)` for lazy evaluation. A project-wide find-and-replace with a regex can handle most cases.

#### [DONE] TASK-129: Tool confirmation handler is a no-op
- **Files:** [`web/events.py`](web/events.py:116)
- **Problem:** TASK-031 added Allow/Deny UI. But the Socket.IO handler for tool confirmation (lines 116-120) accepts the response and does nothing with it. The tool has already executed by the time the confirmation arrives.
- **Fix:** Actually gate tool execution on the confirmation response. This requires the agent loop to pause and wait for the confirmation event.

#### [DONE] TASK-130: Secret key file created with default permissions
- **Files:** [`web/app.py`](web/app.py:109)
- **Problem:** TASK-004 auto-generates a secret key file, but it is created with default filesystem permissions. On shared systems, other users can read the secret and forge session cookies.
- **Fix:** Set file permissions to `0600` (owner read/write only) immediately after creation.

#### [DONE] TASK-131: SRI missing on CDN scripts in index.html
- **Files:** [`web/templates/index.html`](web/templates/index.html)
- **Problem:** TASK-042 was supposed to add integrity hashes for CDN resources (Tailwind, Socket.IO, Marked.js). Verify this was actually done and the hashes are correct.
- **Fix:** Confirm SRI attributes exist with valid hashes. If not, add them. Better yet, vendor the files locally.

#### [DONE] TASK-132: install_addin.py shutil.rmtree with no confirmation
- **Files:** [`scripts/install_addin.py`](scripts/install_addin.py)
- **Problem:** The installer deletes the existing add-in directory with `shutil.rmtree` and no confirmation prompt. If pointed at the wrong directory (or if the path resolution is wrong), it silently deletes whatever is there.
- **Fix:** Prompt for confirmation before deletion. Show the full path being deleted.

#### [DONE] TASK-133: Stale Tailwind CSS v2 dependency
- **Files:** [`web/templates/index.html`](web/templates/index.html)
- **Problem:** The CDN link points to Tailwind v2 which is EOL. v3+ has significant changes and v4 is current.
- **Fix:** Update to Tailwind v4 CDN or vendor locally with a build step.

#### [DONE] TASK-134: Multiple null-check omissions in routes.py
- **Files:** [`web/routes.py`](web/routes.py:497)
- **Problem:** Several routes access attributes on objects that could be `None` without checking. Lines 497-498 and 511-512 are the most obvious, but this pattern appears throughout.
- **Fix:** Add null checks. Return 404 or appropriate error when resource is not found.

#### [DONE] TASK-135: Dead accessor functions in web/app.py
- **Files:** [`web/app.py`](web/app.py:23)
- **Problem:** Functions at lines 23-41 are defined but never called anywhere in the codebase. Dead code.
- **Fix:** Delete them. They are in git history if anyone ever needs them (they will not).

#### [DONE] TASK-136: HTTP polling alongside Socket.IO in app.js
- **Files:** [`web/static/js/app.js`](web/static/js/app.js)
- **Problem:** `setInterval` polling for status updates runs alongside the Socket.IO connection. Socket.IO already provides real-time updates. The polling is redundant and doubles the server load.
- **Fix:** Remove the polling intervals. Use Socket.IO events for all real-time data.

#### [DONE] TASK-137: $ and $$ shadow jQuery conventions in app.js
- **Files:** [`web/static/js/app.js`](web/static/js/app.js)
- **Problem:** `$` and `$$` are defined as DOM query shortcuts, shadowing the universal jQuery convention. If jQuery is ever added (or a library that expects `$`), things break in confusing ways.
- **Fix:** Rename to `qs` / `qsa` or similar. Or just use `document.querySelector` -- it is 2026.

#### [DONE] TASK-138: Inline onclick handlers in HTML templates
- **Files:** [`web/templates/index.html`](web/templates/index.html), [`web/static/js/app.js`](web/static/js/app.js)
- **Problem:** Mix of inline `onclick` attributes and `addEventListener`. Inconsistent, harder to debug, and inline handlers bypass Content Security Policy.
- **Fix:** Move all event handlers to JavaScript via `addEventListener`. Remove all `onclick` attributes.

#### [DONE] TASK-139: Hardcoded DESTRUCTIVE_TOOLS / GEO_TOOLS lists in frontend
- **Files:** [`web/static/js/app.js`](web/static/js/app.js)
- **Problem:** The frontend maintains its own list of which tools are destructive and which are geometric. This duplicates logic from the backend and will inevitably drift out of sync.
- **Fix:** Expose tool metadata via API endpoint. Frontend reads from server, not hardcoded lists.

#### [DONE] TASK-140: LLMResponse missing `reasoning` attribute in __init__
- **Files:** [`ai/providers/base.py`](ai/providers/base.py)
- **Problem:** `LLMResponse` does not initialize `reasoning` in `__init__`, but some code paths set it later. Accessing `response.reasoning` before it is set raises `AttributeError`.
- **Fix:** Initialize `self.reasoning = None` in `__init__`.

#### [DONE] TASK-141: Fresh PromptErrorPolicy created per call
- **Files:** [`ai/error_classifier.py`](ai/error_classifier.py)
- **Problem:** A new `PromptErrorPolicy` object is instantiated on every error classification call. The object contains compiled regexes that are recompiled each time.
- **Fix:** Create once and reuse. Module-level instance or lazy singleton.

#### [DONE] TASK-142: Truncated UUID in conversation IDs
- **Files:** [`ai/conversation_manager.py`](ai/conversation_manager.py)
- **Problem:** Some code paths truncate UUIDs for display, but the truncated form is used as a lookup key. If two conversations share a truncated prefix, the wrong one is loaded.
- **Fix:** Always use full UUID for lookups. Truncate only for display.

#### [DONE] TASK-143: Command UUID never validated on response in bridge
- **Files:** [`fusion/bridge.py`](fusion/bridge.py)
- **Problem:** Commands are sent with a UUID for correlation. Responses include the UUID. But the bridge never checks that the response UUID matches the request UUID. If responses arrive out of order (unlikely with TCP, possible with connection reuse), the wrong result is returned silently.
- **Fix:** Validate UUID match. Log a warning on mismatch.

#### [DONE] TASK-144: Manifest has empty-string key
- **Files:** [`fusion_addin/Fusion360MCP.manifest`](fusion_addin/Fusion360MCP.manifest)
- **Problem:** The JSON manifest contains a key that is an empty string. This is valid JSON but semantically wrong and may confuse Fusion 360's manifest parser.
- **Fix:** Remove the empty key or give it a meaningful name.

#### [DONE] TASK-145: is_connected() redundant wrapper in bridge
- **Files:** [`fusion/bridge.py`](fusion/bridge.py)
- **Problem:** `is_connected()` is a one-line wrapper around the `connected` property. Two ways to check the same thing. Callers use both inconsistently.
- **Fix:** Pick one. Delete the other. Update all callers.

#### [DONE] TASK-146: TimeBudget.__exit__ can replace original exception
- **Files:** [`fusion/bridge.py`](fusion/bridge.py)
- **Problem:** If code inside the `TimeBudget` context manager raises an exception AND the budget is exceeded, `__exit__` replaces the original exception with a timeout error. The real cause is lost.
- **Fix:** If an exception is already propagating (`exc_type is not None`), do not replace it. Log the timeout as additional context.

#### [DONE] TASK-147: Stale model registry -- hardcoded model list drifts from API reality
- **Files:** [`ai/providers/anthropic_provider.py`](ai/providers/anthropic_provider.py)
- **Problem:** The model registry is a hardcoded dict. When Anthropic releases new models or deprecates old ones, the registry is wrong until someone updates the code.
- **Fix:** Fetch model list from API at startup (with fallback to hardcoded list). Or use the Anthropic `models.list()` endpoint.

#### [DONE] TASK-148: Misleading clamp log message
- **Files:** [`ai/providers/anthropic_provider.py`](ai/providers/anthropic_provider.py)
- **Problem:** The log message when clamping max_tokens says one thing but the actual clamping behavior does another (see TASK-099). The log is misleading about what value was actually used.
- **Fix:** Log the actual before and after values of the clamp.

#### [DONE] TASK-149: web_search user agent string is generic
- **Files:** [`ai/web_search.py`](ai/web_search.py)
- **Problem:** The user agent is a generic browser string. Some sites block generic UAs. More importantly, it misrepresents the client identity.
- **Fix:** Use an honest UA: `Artifex360/1.x (AI Design Assistant; +https://github.com/...)`.

### Testing

#### [DONE] TASK-150: No stream_message() happy-path test for AnthropicProvider
- **Files:** [`tests/test_providers.py`](tests/test_providers.py)
- **Problem:** The streaming path -- the one actually used in production -- has no test for the success case. Only the error case is tested.
- **Fix:** Add a test that mocks the Anthropic streaming response and verifies token-by-token output.

#### [DONE] TASK-151: No error response testing in test_web_routes.py
- **Files:** [`tests/test_web_routes.py`](tests/test_web_routes.py)
- **Problem:** All route tests exercise the happy path. No tests verify that error responses have correct status codes, error messages, or do not leak internal details.
- **Fix:** Add tests for: 404 on missing resources, 400 on bad input, 500 handling, error response format.

#### [DONE] TASK-152: test_rules_loader.py reads from actual filesystem
- **Files:** [`tests/test_rules_loader.py`](tests/test_rules_loader.py)
- **Problem:** Tests read from the real `config/rules/` directory. Results depend on what rule files happen to exist. Tests fail or pass based on environment, not code correctness.
- **Fix:** Use `tmp_path` fixture or mock file reads.

#### [DONE] TASK-153: Inconsistent test style -- unittest.TestCase vs pytest
- **Files:** Multiple files in [`tests/`](tests/)
- **Problem:** Some test files use `unittest.TestCase` with `self.assert*`, others use bare pytest `assert`. Some use both in the same file. This makes it harder to understand conventions and use fixtures consistently.
- **Fix:** Pick one style (pytest is simpler). Migrate `unittest.TestCase` classes over time. Not urgent, but stop adding new `TestCase` classes.

#### [DONE] TASK-154: Multiple tests with weak or missing assertions
- **Files:** Multiple files in [`tests/`](tests/)
- **Problem:** Several tests call functions but do not assert anything meaningful about the result. They verify "does not crash" but not "does the right thing." These provide a false sense of coverage.
- **Fix:** Audit tests with zero or one assertion. Add specific assertions about return values, side effects, and state changes.

---

## Archived Tasks (v1.5.0 / v1.6.0 -- All Completed)

<details>
<summary>TASK-001 through TASK-045 -- all implemented 2026-04-14</summary>

### P0 -- SECURITY / DATA LOSS (all resolved)

- **[DONE] TASK-001:** Remote Code Execution via execute_script -- No Sandboxing (`fusion_addin/addin_server.py`)
- **[DONE] TASK-002:** Unauthenticated TCP Server (`fusion_addin/addin_server.py`)
- **[DONE] TASK-003:** Debug Mode + Werkzeug Debugger Exposed to Network (`main.py`)
- **[DONE] TASK-004:** Hardcoded Flask Secret Key (`web/app.py`)
- **[DONE] TASK-005:** Path Traversal in Conversation Persistence (`ai/conversation_manager.py`)
- **[DONE] TASK-006:** Path Traversal in Rules Loader (`ai/rules_loader.py`)
- **[DONE] TASK-007:** Path Traversal in Export Functions (`fusion/bridge.py`, `fusion_addin/addin_server.py`)
- **[DONE] TASK-008:** API Key Double-Encoding on Update (`config/settings.py`)
- **[DONE] TASK-009:** CORS Wildcard on Socket.IO (`web/app.py`)

### P1 -- CRITICAL BUGS (all resolved)

- **[DONE] TASK-010:** Recursive Context Condensation Corruption (`ai/context_manager.py`)
- **[DONE] TASK-011:** LLM-Based Condensation is Dead Code (`ai/context_manager.py`)
- **[DONE] TASK-012:** Thread Safety Disaster in ClaudeClient._run_turn() (`ai/claude_client.py`)
- **[DONE] TASK-013:** execute_script Returns Success Despite Internal Failures (`fusion_addin/addin_server.py`)
- **[DONE] TASK-014:** Agent Goes Silent / Drops User Messages (`ai/claude_client.py`, `web/events.py`)
- **[DONE] TASK-015:** Cancellation is a Stub (`web/events.py`)
- **[DONE] TASK-016:** Default Model ID is Invalid (`config/settings.py`)
- **[DONE] TASK-017:** Checkpoint Restore is Not Atomic (`ai/checkpoint_manager.py`, `ai/claude_client.py`)
- **[DONE] TASK-018:** Reopen Bugs B007 and B010

### P2 -- IMPORTANT IMPROVEMENTS (all resolved)

- **[DONE] TASK-019:** Cut Operations Fail ~85% Due to Missing participantBodies
- **[DONE] TASK-020:** Ambiguous Body Name Resolution
- **[DONE] TASK-021:** Agent Cannot Determine Undo Depth
- **[DONE] TASK-022:** Repetition Warnings Still Ineffective
- **[DONE] TASK-023:** _truncate() Breaks tool_use/tool_result Pairing
- **[DONE] TASK-024:** Image Token Estimation Off by 36x
- **[DONE] TASK-025:** Bare `except Exception: pass` Swallows Critical Errors
- **[DONE] TASK-026:** `datetime.utcnow()` Deprecated
- **[DONE] TASK-027:** Sphere Position Parameter Completely Ignored
- **[DONE] TASK-028:** Cylinder Ignores Z Position
- **[DONE] TASK-029:** Settings Module-Level Singleton Loads at Import Time

### P3 -- CLEANUP / QUALITY (all resolved)

- **[DONE] TASK-030:** Delete Deprecated Tkinter UI
- **[DONE] TASK-031:** Confirmation Modal is Display-Only
- **[DONE] TASK-032:** No Integration Tests for Agent Loop
- **[DONE] TASK-033:** `_run_claude_loop` Calls Private Method `_run_turn`
- **[DONE] TASK-034:** Thread Safety of simulation_mode Flag
- **[DONE] TASK-035:** Version String in Three Places
- **[DONE] TASK-036:** Module-Level Globals for Shared State in web/app.py
- **[DONE] TASK-037:** `get_settings` Route Exposes Internal `_data` Dict
- **[DONE] TASK-038:** Ollama `is_available()` Makes Blocking Network Call Every Time
- **[DONE] TASK-039:** `ALL_GROUPS` Captures State at Import Time
- **[DONE] TASK-040:** No Input Validation on Numeric Parameters
- **[DONE] TASK-041:** Inconsistent Response Schemas
- **[DONE] TASK-042:** CDN Dependencies Without Integrity Hashes
- **[DONE] TASK-043:** f-strings in Logging Calls
- **[DONE] TASK-044:** Streaming Fallback Catches Too Broadly
- **[DONE] TASK-045:** _create_box Uses Wrong Rectangle Semantics

</details>

---

## Version History

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

### v1.5.0 -- Security & Quality Release (2026-04-14)
**P0 Security (9 fixes):** Sandboxed execute_script, added TCP auth tokens, disabled debug mode by default, auto-generated Flask secret keys, fixed 3 path traversal vulnerabilities, fixed API key double-encoding, restricted CORS origins.
**P1 Critical Bugs (9 fixes):** Fixed recursive condensation corruption, revived dead LLM condensation code path, added thread safety to agent turn loop, execute_script now detects partial failures, agent always responds even on errors, implemented real cancellation, fixed invalid default model, made checkpoint restore atomic, reopened B007/B010.
**P2 Improvements (11 fixes):** Auto-populate participantBodies for cuts, smart body name disambiguation, multi-step undo with timeline tracking, escalating repetition enforcement with force-stop, safe truncation respecting tool pairs, fixed 36x image token overestimation, replaced silent exception swallowing with logging, fixed deprecated datetime API, sphere/cylinder position now respected, lazy settings loading.
**P3 Cleanup (16 fixes):** Deleted deprecated Tkinter UI, added Allow/Deny confirmation modal, created agent loop integration tests, made run_turn public API, thread-safe simulation_mode, single-source version string, Flask app extensions pattern, safe settings dict exposure, Ollama availability caching, dynamic tool groups, numeric parameter validation, standardized response schemas, CDN crossorigin attrs, lazy log formatting, narrowed streaming exception catches, fixed box rectangle semantics.

### v1.4.0 -- Context Intelligence + Geometry Awareness (2026-04-13)

<details>
<summary>Details</summary>

#### Context & State Tracking
- [x] Persistent Design State Tracker
- [x] Enhanced pre/post verification delta
- [x] Condensation-resistant state preservation
- [x] Fix context condensation tool_use/tool_result pairing

#### Geometry Understanding
- [x] Sketch coordinate system documentation
- [x] Profile selection guidance
- [x] Pre-cut validation
- [x] Mandatory post-operation verification
- [x] Common API method signatures

#### Screenshot Optimization
- [x] Screenshot budget system
- [x] Selective screenshot capture
- [x] Reduced resolution for intermediate screenshots

#### Error Recovery
- [x] Automatic diagnostic queries on error
- [x] Structured alternative suggestions on repetition
- [x] Variable scope isolation documentation
- [x] 492 total passing tests across 16 test files

</details>

### v1.3.0 -- Autonomous Agent + Rebranding (2026-04-13)

<details>
<summary>Details</summary>

- [x] Autonomous Action Protocol -- complete system prompt rewrite
- [x] Auto-Continue Mechanism -- detect intent-without-action, auto-nudge
- [x] Requirements Clarification -- ask before acting on vague requests
- [x] Project rebranded to Artifex360

</details>

### v1.2.0 -- Real-World Testing Bug Fixes (2026-04-12)

<details>
<summary>Details</summary>

- [x] Fixed undo/redo tools
- [x] Added 10 pre-loaded type shortcuts to execute_script
- [x] Fixed create_sphere
- [x] Increased Ollama timeout
- [x] New delete_body tool
- [x] Fixed save_document for unsaved docs
- [x] Stronger repetition enforcement
- [x] Log sanitizer, diagnostic banner, port change

</details>

### v1.1.0 -- Multi-Provider Support (2026-04-12)

<details>
<summary>Details</summary>

- [x] Provider abstraction layer
- [x] Anthropic + Ollama providers with streaming
- [x] Provider selection UI
- [x] 435 total passing tests

</details>

### v1.0.0 -- Agent Intelligence Layer (2026-04-11)

<details>
<summary>Details</summary>

- [x] Context management / conversation condensation
- [x] Tool repetition detection
- [x] CAD mode system (7 modes)
- [x] Task decomposition / design plan tracking
- [x] Design checkpoint system
- [x] Layered rules/instructions
- [x] 391 total passing tests

</details>

### v0.1.0 through v0.9.0

<details>
<summary>Historical versions (v0.1.0 - v0.9.0)</summary>

- **v0.9.0** -- Agent Intelligence + Platform Optimization
- **v0.8.0** -- Agent Intelligence + Polish
- **v0.7.0** -- Expanded MCP Tools
- **v0.6.0** -- Comprehensive F360 Skill Document
- **v0.5.0** -- Dynamic Script Execution
- **v0.4.0** -- Screenshot + Vision
- **v0.3.0** -- Flask Web App + Agent Foundation
- **v0.2.0** -- Modular Architecture + Tkinter UI (superseded)
- **v0.1.0** -- Initial Scaffold

</details>

---

## auto_hybrid Branch -- Improvements (2026-04-15)

<details>
<summary>All 14 features implemented (14/14)</summary>

### From autoresearch (Karpathy's Autonomous Research Patterns)
- [x] Markdown-as-Skill Protocol System
- [x] Git-Based Design State Management
- [x] Fixed-Budget Operation Timeout
- [x] Context Window Hygiene (Output Filtering)
- [x] Prompt-Based Error Classification

### From Roo Code Provider Optimizations
- [x] Claude Prompt Caching
- [x] Expanded Claude Model Registry
- [x] Ollama Native SDK + Model Discovery
- [x] Claude Reasoning Budget (Extended Thinking)
- [x] Two-Tier Model Cache
- [x] Configurable Ollama num_ctx
- [x] Max Output Token Clamping
- [x] Extended Context (1M Beta)

### Additional Capabilities
- [x] Web Search / Internet Lookup

</details>

---

## Bug Tracker (Historical)

<details>
<summary>B001 through B012</summary>

| ID   | Status   | Priority | Description | Version |
|------|----------|----------|-------------|---------|
| B001 | Resolved | --       | Fusion 360 API import fails outside Fusion environment | v0.1.0 |
| B002 | Resolved | --       | No real Claude API call in v0.1.0 | v0.1.0 |
| B003 | Resolved | P0       | Undo command always fails -- Commands.Undo does not exist | v1.2.0 |
| B004 | Resolved | P0       | Point3D NameError in execute_script | v1.2.0 |
| B005 | Resolved | P0       | Context condensation breaks tool_use/tool_result pairs | v1.3.0 |
| B006 | Resolved | P1       | get_document_info crashes on savePath | v1.2.0 |
| B007 | Reopened | P1       | Agent claims success on failed cut operations | v1.3.0 |
| B008 | Resolved | P1       | save_document fails on new documents | v1.2.0 |
| B009 | Resolved | P2       | Body deletion loop causes index error | v1.3.0 |
| B010 | Reopened | P2       | Ollama 404 errors silently swallow user messages | v1.1.0 |
| B011 | Resolved | P2       | ConstructionPlaneInput.setByPlane() API misuse | v1.3.0 |
| B012 | Resolved | P2       | Git merge conflict in fusion_mcp.log | v1.2.0 |

</details>

---

## Backlog (Future)

- [ ] Export preview (3D viewer in browser)
- [ ] Plugin marketplace for community tools
- [ ] Auto-update mechanism
- [ ] Interactive timeline (click-to-rollback via checkpoints)

---

## Notes

- Fusion 360 API (`adsk.*`) is only available when running **inside** Fusion 360 as an add-in. The bridge module handles both modes gracefully.
- Claude API requires an Anthropic API key configured in the web UI settings panel or `config/config.json`.
- The web app runs at `localhost:8080` and communicates with the Fusion 360 add-in over a local bridge.
- Claude uses the Anthropic tool-use API for a full agent loop: reasoning, tool calls, observation, repeat.
- Multimodal support allows Claude to receive viewport screenshots as image content blocks.
