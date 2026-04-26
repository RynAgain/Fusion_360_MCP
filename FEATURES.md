# Artifex360 -- Code Review Feature Tracker

> AI-powered design intelligence for Fusion 360 -- designs, manipulates, and operates Fusion 360 proficiently through Claude or Ollama qwen3.6.

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

## v1.8.0 -- Roo-Code Parity Improvements (Planned)

Analysis of [Roo-Code](Roo-Code-Analysis/) (the reference codebase behind Roo Code / Cline) identified 21 actionable improvements where our project lags behind production-grade patterns. Tasks are ordered by impact.

### P1 -- High Impact

#### [DONE] TASK-161: Auto-approval system with configurable limits
- **Reference:** [`AutoApprovalHandler.ts`](Roo-Code-Analysis/src/core/auto-approval/AutoApprovalHandler.ts)
- **Problem:** Every tool call either runs unchecked or requires manual confirmation via the (partially implemented) tool confirmation UI. There is no middle ground -- no way to say "auto-approve up to 20 requests or $0.50, then ask me."
- **Fix:** Create `ai/auto_approval.py` with `AutoApprovalHandler` class. Track consecutive auto-approved request count and cumulative cost since last user checkpoint. Configurable `max_auto_requests` and `max_auto_cost` in settings. When limits hit, pause and ask the user whether to continue. Wire into the agent loop in `claude_client.py` before each API call.

#### [DONE] TASK-162: Non-destructive context truncation (tag-and-hide, not delete)
- **Reference:** [`context-management/index.ts`](Roo-Code-Analysis/src/core/context-management/index.ts:67)
- **Problem:** Our [`context_manager.py`](ai/context_manager.py) destructively removes old messages during sliding-window truncation. If the user rewinds past a truncation point, those messages are gone forever.
- **Fix:** Instead of removing messages, tag them with a `truncation_parent` ID and an `is_hidden` flag. Filter hidden messages when building API payloads but keep them in the full history. Add `restore_truncated(truncation_id)` to un-hide messages when rewinding. This aligns with how Roo implements `truncateConversation()` -- messages are tagged, not deleted.

#### [DONE] TASK-163: File access control -- ignore patterns for AI file operations
- **Reference:** [`RooIgnoreController.ts`](Roo-Code-Analysis/src/core/ignore/RooIgnoreController.ts), [`.rooignore`](Roo-Code-Analysis/.rooignore)
- **Problem:** The agent can read and reference any file in the workspace. Sensitive files (`.env`, credentials, private keys) can be inadvertently included in context or tool results. No way for users to exclude files from AI access.
- **Fix:** Create `ai/ignore_controller.py` that loads patterns from a `.artifexignore` file (`.gitignore` syntax via the `pathspec` library). Check all file paths against the ignore controller before reading, including `read_document`, `execute_script` file access, and any file path in tool arguments. Log when a file is blocked. Add `pathspec` to `requirements.txt`.

#### [DONE] TASK-164: Write-protection for configuration files
- **Reference:** [`RooProtectedController.ts`](Roo-Code-Analysis/src/core/protect/RooProtectedController.ts)
- **Problem:** The agent can modify configuration files (`config/settings.py`, `.env`, `fusion_addin/Fusion360MCP.manifest`) if a prompt injection or confused tool call targets them. No guard rails on write operations.
- **Fix:** Create `ai/protected_controller.py` with hardcoded protection patterns: `config/*`, `.env*`, `fusion_addin/*.manifest`, `*.key`, `*.pem`. Any tool that writes or modifies files must check protection status first. Protected files require explicit user confirmation even when auto-approval is enabled (ties into TASK-161).

#### [DONE] TASK-165: Modular system prompt architecture (section-based assembly)
- **Reference:** [`prompts/sections/`](Roo-Code-Analysis/src/core/prompts/sections/) -- 11 separate section modules
- **Problem:** Our [`system_prompt.py`](ai/system_prompt.py) is a single monolithic builder with massive string constants (`CORE_IDENTITY`, `VERIFICATION_PROTOCOL`, etc.) concatenated together. Adding a new section means editing a 360-line file. Conditional sections (e.g., orchestrator protocol) are `if/else` blocks inline.
- **Fix:** Create `ai/prompt_sections/` package with individual modules: `identity.py`, `capabilities.py`, `rules.py`, `tool_use.py`, `modes.py`, `verification.py`, `custom_instructions.py`, `objective.py`. Each module exports a `build(context) -> str` function. `system_prompt.py` becomes a thin assembler that calls each section in order. This matches Roo's `sections/` architecture and makes prompt engineering much more maintainable.

#### [DONE] TASK-166: File context tracking -- detect external file modifications
- **Reference:** [`FileContextTracker.ts`](Roo-Code-Analysis/src/core/context-tracking/FileContextTracker.ts)
- **Problem:** When the agent reads a file via `read_document` or references a file in `execute_script`, and the user modifies that file externally, the agent's context is stale. Subsequent edits or references use outdated content with no warning.
- **Fix:** Create `ai/file_context_tracker.py` that maintains a dict of `{file_path: (last_read_timestamp, content_hash)}`. On each tool call that references a tracked file, compare the current file hash against the stored one. If changed, inject a system message: "File `X` was modified externally since you last read it. Re-read before making changes." Integrate with `watchdog` library for optional real-time filesystem monitoring.

#### [DONE] TASK-167: Folded file context for condensation (signature-only summaries)
- **Reference:** [`foldedFileContext.ts`](Roo-Code-Analysis/src/core/condense/foldedFileContext.ts)
- **Problem:** When we condense conversation context, file contents that were read earlier are either fully retained (wasting tokens) or fully dropped (losing structural awareness). There is no middle ground.
- **Fix:** Create `ai/folded_context.py` that uses Python's `ast` module (for `.py` files) or regex-based extraction (for other languages) to produce signature-only summaries of previously-read files. During condensation, replace full file contents with folded versions showing only class/function/method signatures. Cap total folded content at a configurable character limit (default 50K). This preserves structural awareness at ~10% of the token cost.

#### [DONE] TASK-168: User-defined custom modes via configuration file
- **Reference:** [`modes.ts`](Roo-Code-Analysis/src/shared/modes.ts) -- `getAllModes()` merges custom modes, [`.roomodes`](Roo-Code-Analysis/.roomodes)
- **Problem:** Our [`modes.py`](ai/modes.py) defines 7 hardcoded `CadMode` objects. Users cannot define custom modes without editing source code. Roo allows users to define custom modes in `.roomodes` (JSON/YAML) with custom tool groups, instructions, and file restrictions.
- **Fix:** Add a `config/custom_modes.json` (or `.artifexmodes`) file format. On startup, `modes.py` loads custom modes and merges them with built-in modes (custom overrides built-in on slug collision). Each custom mode specifies: `slug`, `name`, `role_definition`, `tool_groups`, `custom_instructions`, and optional `file_patterns` (restrict which files the mode can reference). Add a `/api/modes` endpoint for CRUD operations on custom modes.

#### [DONE] TASK-181: Dynamic custom tools system -- agent-created tools with test-save-reuse lifecycle
- **Architecture:** [`docs/CUSTOM_TOOLS_ARCHITECTURE.md`](docs/CUSTOM_TOOLS_ARCHITECTURE.md)
- **Problem:** The agent is limited to a fixed set of ~40 built-in tools. Complex or repetitive Fusion 360 operations require lengthy `execute_script` calls each time. Users cannot extend the tool set without modifying source code.
- **Fix:** Create `mcp/custom_tools.py` with `CustomToolRegistry` that manages a full lifecycle: (1) `create_custom_tool` -- define tool name, description, parameters, and a Python implementation script. (2) `test_custom_tool` -- run the tool in the existing `execute_script` sandbox with test parameters. (3) `save_custom_tool` -- persist to `data/custom_tools/` with JSON schema + Python script. (4) `list_custom_tools` / `edit_custom_tool` / `delete_custom_tool` for management. Custom tools are loaded at startup, registered as a dynamic `custom_tools` group in `TOOL_GROUPS`, and appear in the agent's tool list alongside built-in tools. All scripts run through the existing sandbox security layer. Six new meta-tools, 2 new source files, 8 modified files. See architecture doc for full design.

### P2 -- Medium Impact

#### [DONE] TASK-169: Provider confusion bug -- Ollama/Anthropic desync on startup and settings save
- **Files:** [`config/settings.py`](config/settings.py:82), [`ai/providers/provider_manager.py`](ai/providers/provider_manager.py:14), [`ai/claude_client.py`](ai/claude_client.py:238)
- **Problem:** Three bugs caused the system to sometimes use Ollama when Anthropic was configured (or vice versa): (1) `_SETTABLE_KEYS` in settings.py listed `'ollama_url'` instead of `'ollama_base_url'`, causing Ollama URL updates from the web API to be **silently rejected** by TASK-054 validation and never persisted to disk. (2) `ProviderManager.__init__()` hardcoded `_active_type = "anthropic"` regardless of the persisted settings, creating a window where the wrong provider was active before `ClaudeClient` fixed it. (3) Two independent endpoints (`/api/settings` POST and `/api/providers/<type>` POST) could switch providers, potentially desyncing in-memory state from persisted settings.
- **Fix:** (1) Fixed `_SETTABLE_KEYS` to use `'ollama_base_url'` (and added `'ollama_num_ctx'`). (2) `ProviderManager.__init__()` now accepts `initial_provider` parameter. (3) `ClaudeClient.__init__()` passes `settings.provider` to `ProviderManager` at construction. Added startup logging of active provider + model for debuggability.

#### [DONE] TASK-170: Experiment/feature flags system
- **Reference:** [`experiments.ts`](Roo-Code-Analysis/src/shared/experiments.ts)
- **Problem:** New features are either fully on or fully off. No way to gradually enable experimental capabilities, A/B test different behaviors, or let users opt into beta features.
- **Fix:** Create `ai/experiments.py` with an `ExperimentFlags` class. Define flags as an enum: `CODEBASE_SEARCH`, `AUTO_APPROVAL`, `FOLDED_CONTEXT`, `FILE_TRACKING`, `CUSTOM_MODES`. Store enabled/disabled state in settings. Check flags before activating features. Add UI toggle in settings panel. Default all new features to disabled until stable.

#### [DONE] TASK-171: Tool input validation against JSON schema before dispatch
- **Reference:** [`validateToolUse.ts`](Roo-Code-Analysis/src/core/tools/validateToolUse.ts)
- **Problem:** TASK-109 added basic validation in `MCPServer.execute_tool`, but it is shallow -- it checks for missing required fields but does not validate types, ranges, or enum values against the tool's JSON schema definition. Invalid inputs still reach handler code.
- **Fix:** Create `mcp/tool_validator.py` that validates tool inputs against their JSON schema definitions from `TOOL_DEFINITIONS` before dispatching to handlers. Use `jsonschema` library for validation. Return structured error messages listing all validation failures. This catches type mismatches, out-of-range values, and unexpected properties before they hit handler logic.

#### [DONE] TASK-172: Per-conversation todo list tracking
- **Reference:** [`todo.ts`](Roo-Code-Analysis/src/shared/todo.ts), [`UpdateTodoListTool.ts`](Roo-Code-Analysis/src/core/tools/UpdateTodoListTool.ts)
- **Problem:** Our task manager tracks plan steps at the orchestration level, but there is no lightweight per-conversation checklist that the agent can maintain during a single design session. The agent loses track of progress across long conversations.
- **Fix:** Add a `todos` field to conversation metadata in `conversation_manager.py`. Create an `update_todo_list` MCP tool that accepts a markdown checklist string. The agent can create, update, and check off items. Persist with the conversation. Display in the web UI alongside the chat. This is distinct from the orchestrator plan -- it is a lightweight scratch pad the agent uses to track its own progress within a conversation.

#### [DONE] TASK-173: Checkpoint timeout handling with user-facing warnings
- **Reference:** [`checkpoints/index.ts`](Roo-Code-Analysis/src/core/checkpoints/index.ts:19) -- `WARNING_THRESHOLD_MS`, `sendCheckpointInitWarn()`
- **Problem:** Our [`checkpoint_manager.py`](ai/checkpoint_manager.py) has no timeout on checkpoint operations. If querying Fusion 360 state hangs (e.g., Fusion is unresponsive), the checkpoint save blocks indefinitely with no user feedback.
- **Fix:** Add a configurable `checkpoint_timeout` setting (default: 30s). In `CheckpointManager.save()`, wrap the Fusion state queries in a timeout. After 5 seconds, emit a Socket.IO warning event so the UI can show "Checkpoint is taking longer than expected...". On full timeout, save a partial checkpoint with whatever state was retrieved and log a warning. Do not block the agent loop.

#### [DONE] TASK-174: Structured tool base class with common patterns
- **Reference:** [`BaseTool.ts`](Roo-Code-Analysis/src/core/tools/BaseTool.ts) -- common ask/handle/error patterns
- **Problem:** Our tool handlers in `mcp/server.py` and `fusion/bridge.py` are plain functions with duplicated patterns: input validation, error wrapping, logging, result formatting. Each handler reinvents these.
- **Fix:** Create `mcp/base_tool.py` with a `BaseTool` ABC that provides: `validate_input()` (calls JSON schema validator from TASK-171), `execute()` (abstract, implemented by each tool), `format_result()`, `handle_error()`. Convert existing handler functions to tool classes. This reduces boilerplate and ensures consistent behavior across all tools.

#### [DONE] TASK-175: Apply-diff / apply-patch tool for targeted file edits
- **Reference:** [`ApplyDiffTool.ts`](Roo-Code-Analysis/src/core/tools/ApplyDiffTool.ts), [`ApplyPatchTool.ts`](Roo-Code-Analysis/src/core/tools/ApplyPatchTool.ts)
- **Problem:** The agent can only modify project files via `execute_script` (which runs in Fusion 360's Python environment) or `read_document` (read-only). There is no tool for making targeted edits to workspace files -- a critical gap for config file updates, rule file management, or project file modifications.
- **Fix:** Create `apply_diff` and `write_file` MCP tools. `apply_diff` accepts a file path and a search/replace block format (matching Roo's format). `write_file` creates or overwrites a file. Both must check file protection (TASK-164) and ignore patterns (TASK-163). Add to a new `file_ops` tool group. This enables the agent to maintain its own configuration, update rule files, and manage project assets.

#### [DONE] TASK-176: Dedicated summarization provider for context condensation
- **Reference:** Roo's condense module uses the task's API handler (similar issue)
- **Problem:** TASK-086 identified that condensation quality is poor with small Ollama models. The fix was documented but the implementation is incomplete -- there is no actual mechanism to route condensation to a different provider than the main conversation.
- **Fix:** Add `summarization_provider` and `summarization_model` to settings. In `context_manager.py`, when `_llm_summarize()` is called, check if a separate summarization provider is configured. If so, create a temporary provider instance for the summary call. Default behavior: use the main provider (backward compatible). This allows users to run a small local model for chat but use Claude for high-quality condensation.

### P3 -- Lower Impact / Quality

#### [DONE] TASK-177: Internationalization (i18n) foundation
- **Reference:** [`i18n/`](Roo-Code-Analysis/src/i18n/), 14 language files in `package.nls.*.json`
- **Problem:** All user-facing strings (error messages, tool descriptions, UI labels, system prompts) are hardcoded in English. No mechanism for translation or localization.
- **Fix:** Create `ai/i18n.py` with a `t(key, **kwargs)` function that loads translations from JSON files in `config/locales/`. Start with English (`en.json`) as the source of truth. Add a `language` setting. Migrate user-facing strings in `web/routes.py`, `web/events.py`, and tool descriptions to use `t()`. This is a foundation -- full translation can happen incrementally.

#### [DONE] TASK-178: Structured telemetry service for operation tracking
- **Reference:** [`TelemetryService`](Roo-Code-Analysis/src/core/context-management/index.ts:4) -- used throughout for tracking operations
- **Problem:** We log operations via Python's logging module, but there is no structured telemetry. We cannot answer questions like: "How many tool calls per conversation on average?", "What is the condensation frequency?", "Which tools fail most often?"
- **Fix:** Create `ai/telemetry.py` with a `TelemetryService` singleton that records structured events: `tool_call(name, duration, success)`, `api_call(provider, model, tokens_in, tokens_out, cost)`, `condensation(before_tokens, after_tokens)`, `checkpoint(action, duration)`. Store in a local SQLite database (`data/telemetry.db`). Add a `/api/telemetry/summary` endpoint for basic analytics. All collection is local-only, opt-in, and privacy-respecting.

#### [DONE] TASK-179: Message queue service abstraction
- **Reference:** [`MessageQueueService.ts`](Roo-Code-Analysis/src/core/message-queue/MessageQueueService.ts)
- **Problem:** Our [`message_queue.py`](ai/message_queue.py) is functional but tightly coupled to the Socket.IO emission pattern. No abstraction layer for different delivery mechanisms (WebSocket, REST polling, file-based for testing).
- **Fix:** Define a `MessageSink` Protocol with `emit(event, data)` method. Create `SocketIOSink`, `LoggingSink` (for testing), and `FileSink` (for debugging). `MessageQueue` accepts a `MessageSink` instead of directly calling `socketio.emit()`. This improves testability and allows alternative delivery mechanisms.

#### [DONE] TASK-180: Configurable condensation thresholds in settings UI
- **Reference:** [`MIN_CONDENSE_THRESHOLD`, `MAX_CONDENSE_THRESHOLD`](Roo-Code-Analysis/src/core/condense/index.ts:7) -- imported as configurable constants
- **Problem:** TASK-124 moved magic numbers to constants but they are still hardcoded in `context_manager.py`. Users cannot tune condensation behavior without editing source code. Aggressive condensation loses CAD state; lazy condensation risks context overflow.
- **Fix:** Add `condense_threshold` (default 0.65), `preserve_recent_turns` (default 4), and `condense_strategy` (options: `llm`, `rule_based`, `hybrid`) to settings. Expose in settings UI. `ContextManager.__init__()` reads from settings instead of module constants. Document the tradeoffs for each setting value.

---

## v1.8.0 Code Review -- Findings (2026-04-20)

> Review of the 17+ new modules added in the v1.8.0 Roo-Code parity implementation.
>
> I just got handed 17 new source files written in a single AI session and told to "review them." Seventeen files. One session. You know what else gets built in one session? Sand castles. And they have about the same structural integrity as what I found here. The happy-path code is fine -- competent even. But security? The custom tools system has a script injection hole you could sail an aircraft carrier through. The ignore controller claims to implement .gitignore semantics but actually uses fnmatch (those are not the same thing and everyone who has ever written a .gitignore file knows it). The telemetry service commits to SQLite on every single event write. The folded context generator produces duplicate entries for every class method. And the prompt section modules accept a `context` parameter that five out of six of them completely ignore. Here are 32 tasks. Every one is a real bug I found in actual code.

### P0 -- Critical

#### [ ] TASK-182: Custom tools script injection via triple-quote breakout in params_json
- **Files:** [`mcp/custom_tools.py`](mcp/custom_tools.py:213) (lines 213-222), [`mcp/custom_tools.py`](mcp/custom_tools.py:399) (lines 399-408)
- **Problem:** User-controlled `params_json` is injected into a Python script template using triple-quoted strings (`'''`). If the serialized JSON contains a literal `'''` (e.g., a parameter value with three consecutive single quotes), it breaks out of the string literal and injects arbitrary Python code into the wrapped script. The `validate_script()` function at line 92 only scans the tool's *script body* -- it never validates the injected params. This completely bypasses all security checks. A crafted tool input like `{"value": "x'''\\nimport os; os.system('rm -rf /')\\n'''"}` escapes the string and executes arbitrary code.
- **Fix:** Use `json.dumps()` to produce the params string (it already does), but embed it with escaped quotes or use a unique delimiter that cannot appear in valid JSON. Better: write params to a temp file and have the script read from it, or pass via environment variable. Never interpolate untrusted data into code templates.

#### [ ] TASK-183: Custom tools validate_script() regex blocklist is trivially bypassable
- **Files:** [`mcp/custom_tools.py`](mcp/custom_tools.py:78) (lines 78-101)
- **Problem:** The forbidden pattern list has at least 6 known bypasses: (1) `importlib.import_module('os')` bypasses `\bimport\s+os\b`. (2) `from os import system` is not matched. (3) `builtins.open()` bypasses `\bopen\s*\(`. (4) `getattr(__builtins__, 'exec')(code)` with string concatenation bypasses `\bexec\s*\(`. (5) `__import__` can be aliased via `x = vars()['__imp' + 'ort__']`. (6) `compile` is caught but `types.CodeType()` is not. A regex blocklist is fundamentally the wrong approach for sandboxing. The existing `execute_script` sandbox in `addin_server.py` (fixed in TASK-046) is the actual security boundary, but these "warnings" give false confidence.
- **Fix:** Either make `validate_script()` return hard errors (not warnings) and use an AST-based analysis that walks imports and attribute access, or remove it entirely and rely solely on the `execute_script` sandbox. Do not ship security theater that makes people think scripts are validated when they are not.

#### [ ] TASK-184: file_tools.py write_file missing ignore-pattern check -- can write to .env
- **Files:** [`mcp/file_tools.py`](mcp/file_tools.py:83) (lines 83-128)
- **Problem:** `apply_diff()` correctly checks both `get_protected_controller().is_protected()` (line 36) and `get_ignore_controller().is_blocked()` (line 45) before writing. But `write_file()` only checks `is_protected()` (line 106) and completely skips the ignore check. The agent can create or overwrite `.env` files, `*.key` files, and anything in `secrets/` or `credentials/` directories via `write_file` even though they are blocked for reading. This is an asymmetric access control failure -- read is blocked but write is wide open.
- **Fix:** Add `get_ignore_controller().is_blocked(abs_path)` check to `write_file()` between the protection check and the file existence check, identical to `apply_diff()`.

#### [ ] TASK-185: file_tools.py path traversal check has prefix collision vulnerability
- **Files:** [`mcp/file_tools.py`](mcp/file_tools.py:31) (line 31, line 101)
- **Problem:** `abs_path.startswith(os.path.normpath(root))` is used for path traversal prevention. This has two flaws: (1) If the project root is `/home/user/project`, a path resolving to `/home/user/project_evil/malicious.py` passes the `startswith` check because the string `/home/user/project_evil` starts with `/home/user/project`. (2) On Windows, `normpath` does not resolve symlinks, so a symlink inside the project pointing outside bypasses the check entirely.
- **Fix:** Append `os.sep` to the normalized root before comparison: `abs_path.startswith(os.path.normpath(root) + os.sep)`. Or better, use `os.path.commonpath([abs_path, root]) == root` or `PurePath(abs_path).is_relative_to(root)` (Python 3.9+).

### P1 -- High

#### [DONE] TASK-186: ignore_controller uses fnmatch, not gitignore semantics -- multiple pattern failures
- **Files:** [`ai/ignore_controller.py`](ai/ignore_controller.py:8) (line 8, lines 101-146)
- **Problem:** The module docstring (line 7) claims "Uses .gitignore-style pattern matching" but uses `fnmatch` which is fundamentally different. Failures: (1) Negation patterns (`!important.env`) are not supported -- they will be treated as literal filenames. (2) Directory-only patterns (`build/`) are not distinguished from file patterns. (3) `**` recursive matching at lines 116-145 is a hand-rolled approximation that misses cases like `a/**/b` matching `a/x/y/z/b` (multiple intermediate directories). (4) The `pathspec` library was mentioned in TASK-163's fix description but was never actually used. The TASK specified "`.gitignore` syntax via the `pathspec` library" and `pathspec` is not in `requirements.txt`.
- **Fix:** Replace `fnmatch`-based matching with the `pathspec` library (which correctly implements gitignore semantics). Add `pathspec` to `requirements.txt`. This is a one-function replacement that fixes all four pattern matching failures at once.

#### [DONE] TASK-187: summarization.py assumes response.content is a string -- it is a list of blocks
- **Files:** [`ai/summarization.py`](ai/summarization.py:79) (line 79)
- **Problem:** `return response.content if response else None` returns `response.content` directly. But in the provider layer (`ai/providers/base.py`), `LLMResponse.content` is a `list[dict]` of content blocks (e.g., `[{"type": "text", "text": "..."}]`), not a plain string. This means `SummarizationService.summarize()` returns a list where every caller expects a string. The `fallback_client.summarize()` path at line 85 presumably returns a string, making the two code paths return incompatible types.
- **Fix:** Extract text from content blocks: `return "".join(b.get("text", "") for b in response.content if b.get("type") == "text")`. Or check what the fallback path returns and make them consistent.

#### [DONE] TASK-188: folded_context.py ast.walk produces duplicate signatures for class methods
- **Files:** [`ai/folded_context.py`](ai/folded_context.py:59) (lines 59-91)
- **Problem:** `ast.walk(tree)` traverses the entire AST recursively. When it encounters a `ClassDef` node, it appends a class signature. When it later encounters the `FunctionDef` nodes that are children of that class, it appends them again as standalone functions. Result: every method appears twice in the output -- once inside the class (if the class body were shown) and once as a top-level function. For a file with 5 classes averaging 8 methods each, the output is ~40% duplicates, wasting tokens.
- **Fix:** Use `ast.iter_child_nodes(tree)` for top-level nodes only, then recursively handle class bodies manually. Or track parent nodes and skip functions that are children of classes (emit them as part of the class instead).

#### [DONE] TASK-189: custom_tools.py edit_tool skips version/timestamp increment on script warnings
- **Files:** [`mcp/custom_tools.py`](mcp/custom_tools.py:298) (lines 298-319)
- **Problem:** At line 303-307, if a script edit produces warnings, the method returns early with `{"success": True, ...}` BEFORE lines 311-312 which increment `tool.updated_at` and `tool.version`. So a tool edited with a warning-producing script gets the new script content but keeps the old version number and timestamp. The next non-warning edit will then show a version jump of 1 instead of 2, and the timestamp will be wrong. Additionally, the early return skips the persistence at lines 315-316, so saved tool edits with warnings are lost on restart.
- **Fix:** Move `tool.updated_at = time.time()` and `tool.version += 1` before the warning check. Move the persistence call before the early return, or restructure to have a single return path.

#### [DONE] TASK-190: modes.py allows custom modes to shadow safety-critical built-in modes
- **Files:** [`ai/modes.py`](ai/modes.py:330) (lines 330-335), [`ai/modes.py`](ai/modes.py:377) (line 377-384)
- **Problem:** In `ModeManager.__init__()` at line 334, custom modes override built-in modes with only an info-level log. A malicious or careless `custom_modes.json` can replace the `"orchestrator"` mode (which is intentionally restricted to read-only tools) with one that has full tool access. It can replace `"full"` mode to add restrictions that lock the user out. `add_custom_mode()` at line 377 has the same issue -- no protection against overriding built-in slugs. `remove_custom_mode()` at line 392 correctly prevents removal, but override is unrestricted.
- **Fix:** In both `__init__()` and `add_custom_mode()`, refuse to override built-in mode slugs (or at minimum, require an explicit `override_builtin=True` parameter). Log at WARNING level, not INFO.

#### [DONE] TASK-191: telemetry.py close() races with record() -- NPE on concurrent access
- **Files:** [`ai/telemetry.py`](ai/telemetry.py:138) (lines 138-142)
- **Problem:** `close()` sets `self._conn = None` at line 141 without acquiring `self._lock`. A concurrent `record()` call can pass the `if not self._conn` guard at line 74, then `close()` sets `_conn = None`, then `record()` calls `self._conn.execute()` on `None` -- raising `AttributeError`. Same race exists between `close()` and `get_summary()`.
- **Fix:** Acquire `self._lock` in `close()` before setting `self._conn = None`. Or use a flag like `self._closing = True` that `record()` checks.

#### [DONE] TASK-192: custom_tools.py registry has no thread safety on dict mutations
- **Files:** [`mcp/custom_tools.py`](mcp/custom_tools.py:104) (entire `CustomToolRegistry` class)
- **Problem:** `_saved` and `_drafts` dicts are mutated by `create_draft()`, `save_tool()`, `edit_tool()`, `delete_tool()`, and `save_tool_direct()` with no locking. `execute_custom_tool()` reads `_saved` without a lock. If the agent loop calls `execute_custom_tool()` while another thread (e.g., a web API handler) calls `save_tool()` or `edit_tool()`, dict mutation during iteration raises `RuntimeError: dictionary changed size during iteration`. This is the same class of bug that TASK-049 and TASK-104 fixed elsewhere.
- **Fix:** Add a `threading.Lock` to `CustomToolRegistry`. Acquire it around all dict mutations and reads that depend on consistent state.

#### [DONE] TASK-193: i18n.py format string injection via Python format spec mini-language
- **Files:** [`ai/i18n.py`](ai/i18n.py:71) (lines 70-74)
- **Problem:** `text.format(**kwargs)` uses Python's full format string engine. If a translation value in `en.json` (or a future locale file written by a contributor) contains `{arg.__class__.__mro__}` or `{arg.__init__.__globals__}`, and `kwargs` passes an object, the format spec mini-language allows attribute access and can leak internal Python types and potentially secrets. The `except (KeyError, IndexError)` at line 73 does NOT catch `AttributeError` from attribute traversal, so these attacks raise uncaught exceptions rather than being silently dangerous, but they still cause crashes.
- **Fix:** Use `string.Template.safe_substitute()` instead of `str.format()`. Or sanitize translation strings to reject any format spec containing `.` (attribute access). At minimum, catch `AttributeError` and `TypeError` in the except clause.

### P2 -- Medium

#### [DONE] TASK-194: checkpoint_manager timeout is advisory only -- Fusion hangs block forever
- **Files:** [`ai/checkpoint_manager.py`](ai/checkpoint_manager.py:54) (lines 69-138)
- **Problem:** TASK-173 added `_timeout` and `_warning_threshold` parameters and a `_check_warning()` helper, but the timeout is never enforced. `_check_warning()` only emits a warning callback -- it does not interrupt the blocking `mcp_server.execute_tool()` calls at lines 89 and 105. The `if elapsed >= self._timeout` checks at lines 96 and 110 only trigger inside exception handlers -- they fire when an exception happens to coincide with timeout expiry, not when the timeout itself is reached. If Fusion 360 hangs without raising an exception, the checkpoint blocks the calling thread indefinitely.
- **Fix:** Run the Fusion state queries in a `concurrent.futures.ThreadPoolExecutor` with `future.result(timeout=self._timeout)`. Catch `TimeoutError` explicitly and save a partial checkpoint with whatever state was retrieved.

#### [DONE] TASK-195: prompt_sections ignore their context parameter -- cannot be conditional
- **Files:** [`ai/prompt_sections/identity.py`](ai/prompt_sections/identity.py:4), [`ai/prompt_sections/capabilities.py`](ai/prompt_sections/capabilities.py:4), [`ai/prompt_sections/workflow.py`](ai/prompt_sections/workflow.py:4), [`ai/prompt_sections/rules.py`](ai/prompt_sections/rules.py:4), [`ai/prompt_sections/verification.py`](ai/prompt_sections/verification.py:4)
- **Problem:** Five of six prompt section modules accept `context: dict` but return hardcoded strings regardless of the context. Only [`custom_instructions.py`](ai/prompt_sections/custom_instructions.py:7) reads from context. This means the modular prompt architecture (TASK-165) cannot produce mode-specific, settings-dependent, or state-aware prompts. The identity section cannot adapt to the current mode. The rules section cannot include or exclude rules based on settings. The entire point of passing context is defeated.
- **Fix:** At minimum, each section should check `context.get("mode")` and adjust content accordingly. The rules section should check `context.get("settings", {})` for feature flags. The capabilities section should list only tools available in the current mode. If context is truly never needed, remove the parameter to avoid misleading future developers.

#### [DONE] TASK-196: tool_validator.py boolean passes integer validation
- **Files:** [`mcp/tool_validator.py`](mcp/tool_validator.py:84) (lines 84-97)
- **Problem:** The type map at line 88 maps `"integer"` to `int`. In Python, `bool` is a subclass of `int` (`isinstance(True, int)` is `True`). So a tool parameter defined as `{"type": "integer"}` will accept `True`/`False` without error. This masks bugs where the LLM sends a boolean where a number was expected (e.g., `{"count": true}` instead of `{"count": 1}`).
- **Fix:** Check `isinstance(value, bool)` before `isinstance(value, int)` and reject booleans for integer fields. Or use `type(value) is int` for strict type checking.

#### [DONE] TASK-197: tool_validator.py has no nested schema validation
- **Files:** [`mcp/tool_validator.py`](mcp/tool_validator.py:42) (lines 74-98)
- **Problem:** Validation only checks top-level properties. If a parameter schema defines nested objects (`{"type": "object", "properties": {...}}`) or typed arrays (`{"type": "array", "items": {"type": "string"}}`), the nested structure is never validated. An object property could contain anything, and an array could contain mixed types, all passing validation. This undermines the purpose of schema validation.
- **Fix:** Implement recursive validation for `"type": "object"` (validate nested properties) and `"type": "array"` (validate items against items schema). Or use the `jsonschema` library as TASK-171 originally specified.

#### [DONE] TASK-198: file_context_tracker.py reads entire files into memory for hash comparison
- **Files:** [`ai/file_context_tracker.py`](ai/file_context_tracker.py:80) (lines 80-82)
- **Problem:** `check_modified()` opens the file and reads it entirely into memory (`f.read()`) then hashes it. `get_stale_files()` at line 91 calls `check_modified()` for every tracked file. For a session tracking 20+ files including large exports (3MF, STL), this reads potentially hundreds of MB into memory on every check cycle. `record_read()` at line 43-44 does the same.
- **Fix:** Use chunked reading: `while chunk := f.read(8192): h.update(chunk)`. Or use `os.stat().st_mtime` as a fast preliminary check and only hash on mtime change.

#### [DONE] TASK-199: telemetry.py commit-per-event kills write performance
- **Files:** [`ai/telemetry.py`](ai/telemetry.py:82) (line 82)
- **Problem:** Every `record()` call does `self._conn.commit()` which forces an fsync to disk. At high event rates (every tool call, every API call), this becomes the bottleneck. SQLite in default journal mode can only do ~60 commits/second on spinning disk. Even on SSD, this is unnecessary overhead.
- **Fix:** Set WAL mode in `_init_db()`: `self._conn.execute("PRAGMA journal_mode=WAL")`. Batch commits: accumulate events in a list and flush every N events or every T seconds via a background timer. Or simply remove the per-event commit and rely on SQLite's auto-commit at connection close.

#### [DONE] TASK-200: folded_context.py skipped file count is incorrect at character limit
- **Files:** [`ai/folded_context.py`](ai/folded_context.py:130) (lines 130-132)
- **Problem:** When `total_chars >= max_characters` at line 130, the remaining files are counted as skipped via `skipped += len(file_paths) - processed - skipped`. But `processed` has already been incremented for files that were processed earlier in the loop, and `skipped` has been incremented for non-Python files and failed parses. The formula `len(file_paths) - processed - skipped` gives the number of remaining files correctly ONLY if the current file is the one that triggered the limit. But the `break` happens before incrementing `processed` for the current file, so the current file is double-counted in the skip total.
- **Fix:** Calculate remaining as `len(file_paths) - (processed + skipped)` before the break, which accounts for the current iteration. Or restructure to use `enumerate()` and compute remaining from the index.

#### [DONE] TASK-201: context_manager.py truncate_nondestructive messages_retained count is wrong
- **Files:** [`ai/context_manager.py`](ai/context_manager.py:424) (lines 424-430)
- **Problem:** `truncate_nondestructive()` inserts a marker message at line 424, increasing the list length by 1. But `messages_retained` at line 429 is computed as `len(messages) - hide_count`, which is `(original_len + 1) - hide_count`. The "retained" count includes the marker as a retained message, which is misleading -- the marker is metadata, not a real message. Any UI displaying "X messages retained" will be off by one.
- **Fix:** Compute `messages_retained` before inserting the marker, or explicitly subtract 1 for the marker: `messages_retained = len(messages) - hide_count - 1`.

#### [DONE] TASK-202: auto_approval.py cost tracking uses floating-point comparison
- **Files:** [`ai/auto_approval.py`](ai/auto_approval.py:84) (line 84)
- **Problem:** `self._cumulative_cost >= self._max_cost` compares accumulated floats. After many small additions (e.g., 0.001 added 1000 times), IEEE 754 floating-point arithmetic can produce `0.9999999999999998` instead of `1.0`, causing the limit check to pass when it should fail. The `round()` in `to_dict()` at lines 112-113 masks this from the UI, but the actual comparison at line 84 uses unrounded values.
- **Fix:** Use `decimal.Decimal` for cost tracking, or multiply by 10000 and track as integer cents/thousandths. Or add epsilon: `self._cumulative_cost >= self._max_cost - 1e-10`.

#### [DONE] TASK-203: ExperimentFlags._defaults is a mutable class-level dict
- **Files:** [`ai/experiments.py`](ai/experiments.py:48) (line 48)
- **Problem:** `_defaults: dict[str, bool] = {flag.value: False for flag in ExperimentId}` is a mutable class variable shared across all instances (and the class itself). If any code accidentally does `ExperimentFlags._defaults["new_flag"] = True` or `instance._defaults.update(...)`, it mutates the dict for every instance. While the current code only reads from it, this is a latent mutation bug waiting to happen.
- **Fix:** Use `types.MappingProxyType({...})` to make it immutable, or compute the defaults fresh in each method that needs them.

### P3 -- Low

#### [DONE] TASK-204: auto_approval.py _last_reset_index is dead code
- **Files:** [`ai/auto_approval.py`](ai/auto_approval.py:41) (line 41)
- **Problem:** `self._last_reset_index = 0` is initialized in `__init__` but never read or updated by any method in the class. It is not included in `to_dict()`, not updated in `reset()`, and not checked in `check_limits()`. Dead code.
- **Fix:** Remove `_last_reset_index` from `__init__`. If it was intended for tracking the message index at last reset, implement it or delete it.

#### [DONE] TASK-205: message_sink.py MultiplexSink iterates list during possible concurrent modification
- **Files:** [`ai/message_sink.py`](ai/message_sink.py:93) (lines 93-100)
- **Problem:** `emit()` iterates `self._sinks` directly. `add()` at line 87 appends to the list. `remove()` at line 91 creates a new list. If `add()` is called from another thread during `emit()`'s iteration, `RuntimeError: list changed size during iteration` is raised. The `remove()` method is safe because it creates a new list, but `add()` mutates in-place.
- **Fix:** Iterate a snapshot: `for sink in list(self._sinks):` in `emit()`. Or use a `threading.Lock` around mutations and iteration.

#### [DONE] TASK-206: i18n.py _translations module global has no thread safety
- **Files:** [`ai/i18n.py`](ai/i18n.py:19) (lines 19-20, 38-43, 64-66)
- **Problem:** `_translations` and `_current_language` are module-level mutable globals modified by `set_language()` and read by `t()`. No synchronization. If two threads call `set_language("fr")` and `set_language("de")` concurrently, `_current_language` can end up as either value while `_translations` has entries loaded from the other. The `t()` function at line 64 checks and potentially loads a locale, creating a TOCTOU race.
- **Fix:** Use a `threading.Lock` around `set_language()` and the lazy-load path in `t()`. Or use `threading.local()` for per-thread language selection.

#### [DONE] TASK-207: folded_context.py references deprecated ast.Str node type
- **Files:** [`ai/folded_context.py`](ai/folded_context.py:70) (line 70, line 84)
- **Problem:** `isinstance(node.body[0].value, (ast.Str, ast.Constant))` includes `ast.Str` which was deprecated in Python 3.8 and removed in Python 3.12. On Python 3.12+, `ast.Str` does not exist and this line raises `AttributeError`. The code tries to handle both old and new AST node types but will crash on modern Python.
- **Fix:** Remove `ast.Str` and use only `ast.Constant`. Check `isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str)` for the docstring check.

#### [DONE] TASK-208: test_custom_tools.py has zero tests for script injection attack vectors
- **Files:** [`tests/test_custom_tools.py`](tests/test_custom_tools.py)
- **Problem:** 661 lines of thorough tests -- CRUD, round-trip, edge cases -- but zero tests for the P0 security vulnerability (TASK-182). No test sends params containing `'''` to verify the triple-quote breakout is handled. No test checks that `validate_script()` catches `from os import system` or `importlib.import_module`. The `TestValidateScript` class at line 196 tests the happy path of detection but not the known bypasses.
- **Fix:** Add a `TestScriptInjection` class with tests for: (1) params_json containing `'''`, (2) `from os import system`, (3) `importlib.import_module('os')`, (4) `builtins.open()`, (5) `getattr(__builtins__, 'exec')`. These should be the FIRST tests written after fixing TASK-182 and TASK-183.

#### [DONE] TASK-209: test_ignore_controller.py Windows drive letter edge case
- **Files:** [`tests/test_ignore_controller.py`](tests/test_ignore_controller.py:139) (line 139)
- **Problem:** `test_custom_pattern_blocks_matching_file` passes an absolute `tmp_path` joined path to `is_blocked()`. The `is_blocked()` method uses `os.path.relpath()` to convert absolute to relative. On Windows, if `tmp_path` is on a different drive letter than `project_root`, `os.path.relpath()` raises `ValueError` (caught at line 95, falls through to raw path). The test will produce different results on different Windows drive configurations.
- **Fix:** Use relative paths in the test assertion, or mock `os.path.relpath` to ensure consistent behavior. Better: the test should pass a path relative to `tmp_project`, not an absolute path.

#### [DONE] TASK-210: ignore/protected controller singletons not resettable without private access
- **Files:** [`ai/ignore_controller.py`](ai/ignore_controller.py:155) (lines 155-163), [`ai/protected_controller.py`](ai/protected_controller.py:87) (lines 87-96)
- **Problem:** Both controllers use module-level singleton patterns with `_ignore_controller` / `_protected_controller` globals. Tests must reach into private module state (`mod._ignore_controller = None`) to reset them (as seen in [`test_ignore_controller.py`](tests/test_ignore_controller.py:264) line 264). There is no public `reset()` function. This makes test isolation fragile and couples tests to implementation details.
- **Fix:** Add a `reset_ignore_controller()` and `reset_protected_controller()` public function to each module. Or better, accept the controller as a parameter in the functions that use them (dependency injection) instead of relying on module-level singletons.

#### [DONE] TASK-211: protected_controller.py has less thorough ** pattern handling than ignore_controller
- **Files:** [`ai/protected_controller.py`](ai/protected_controller.py:72) (lines 72-83)
- **Problem:** `ProtectedController.is_protected()` handles `**` patterns (lines 79-82) with only one simplification pass (`**/` -> `*/`). But `IgnoreController.is_blocked()` at lines 116-145 has three additional fallback checks: stripping leading `**/`, simplifying stripped patterns, and root-match fallback. The two controllers use the same pattern style but have inconsistent matching logic. A path that is correctly blocked by the ignore controller may not be correctly detected as protected.
- **Fix:** Extract the pattern matching logic into a shared `_match_pattern(rel_path, filename, pattern)` function used by both controllers. This ensures consistent behavior and eliminates the duplicated-but-divergent code.

#### [DONE] TASK-212: base_tool.py to_definition omits input_schema for schema-less tools
- **Files:** [`mcp/base_tool.py`](mcp/base_tool.py:101) (lines 101-112)
- **Problem:** When `self.schema` is `None` or falsy (line 108), `to_definition()` omits `input_schema` from the returned dict entirely. The MCP protocol specification requires tools to declare their input schema, even if it is just `{"type": "object"}` with no properties. Omitting it may cause protocol validation failures in strict MCP clients.
- **Fix:** Always include `input_schema`: if `self.schema` is None, use `{"type": "object", "properties": {}}` as the default.

#### [DONE] TASK-213: base_tool.py exception logging uses logger.exception in non-exception context
- **Files:** [`mcp/base_tool.py`](mcp/base_tool.py:98) (line 98)
- **Problem:** `logger.exception()` at line 98 is correct (it is inside an except block). However, `str(exc)` is also passed as the error message in the `ToolResult` at line 99. If the exception contains sensitive information (file paths, credentials from a failed operation), this is returned to the LLM and potentially to the user. TASK-055 fixed this pattern in Socket.IO handlers but the same issue exists here.
- **Fix:** Return a generic error message for unexpected exceptions: `error="Internal tool error"`. Log the full exception server-side (which is already done). Only return specific error messages for expected exception types.

---

## v1.9.0 -- Conversation Log Audit (2026-04-20)

Analysis of 12 real conversation logs (90+ user messages, 30+ web searches, 50+ tool calls) identified 12 actionable improvements across web infrastructure, Fusion 360 scripting, agent loop management, token efficiency, and conversation handling. Tasks are ordered by impact within each category.

### Web Search & Fetch Infrastructure

#### [ ] TASK-214: Web search returns empty results consistently
- **Files:** [`ai/web_search.py`](ai/web_search.py)
- **Problem:** `web_search` returns `{"status": "success", "results": []}` for the vast majority of legitimate queries across 4+ conversations (~30+ empty results). The agent wastes enormous token budget retrying different query formulations. Priority: HIGH.
- **Fix:** Investigate search provider integration. Add diagnostic logging for raw API responses. If the provider is non-functional, fail fast with a clear error instead of returning empty success. Consider a fallback search provider or direct URL fetch as an alternative path.

#### [ ] TASK-215: web_fetch on PDFs returns raw binary instead of extracted text
- **Files:** [`ai/web_search.py`](ai/web_search.py), [`ai/document_extractor.py`](ai/document_extractor.py)
- **Problem:** When `web_fetch` fetches a PDF URL, it returns raw binary (`%PDF-1.5...`) instead of extracting text. The `read_document` tool handles local PDFs via `document_extractor.py`, but `web_fetch` does not apply the same extraction pipeline. Priority: HIGH.
- **Fix:** Detect PDF content-type in `fetch_page()` response headers. When a PDF is detected, write to a temp file and route through `DocumentExtractor.extract_pdf()` before returning text to the agent. Reuse the existing PyMuPDF pipeline from TASK-160.

#### [ ] TASK-216: Error classifier gives CAD-specific suggestions for web tool errors
- **Files:** [`ai/error_classifier.py`](ai/error_classifier.py)
- **Problem:** When `web_fetch` gets a 404, the error is classified as `REFERENCE_ERROR` with suggestion "use get_body_list or get_component_info" which makes no sense for web URLs. Should have web-specific error suggestions. Priority: MEDIUM.
- **Fix:** Add tool-category awareness to error classification. Web tool errors should suggest URL verification, alternative URLs, or fallback to `web_search`. CAD suggestions should only appear for CAD tool errors.

#### [ ] TASK-217: Repetition detector gives CAD suggestions for web tool repetition
- **Files:** [`ai/repetition_detector.py`](ai/repetition_detector.py)
- **Problem:** When web tools are called multiple times with different queries, the repetition warning suggests "Verify current design state with get_body_list" -- irrelevant for web research tasks. Should be tool-category-aware. Priority: MEDIUM.
- **Fix:** Add tool-category metadata to the repetition detector. Web tool repetition should suggest asking the user for direct URLs or falling back to internal knowledge. CAD tool repetition keeps existing suggestions.

### Fusion 360 API / Scripting

#### [DONE] TASK-218: Timeline Surgical Editing -- Avoid Full-Project Rebuild
- **Files:** [`fusion_addin/addin_server.py`](fusion_addin/addin_server.py), [`config/rules/fusion_design_iteration.md`](config/rules/fusion_design_iteration.md), [`docs/F360_SKILL.md`](docs/F360_SKILL.md)
- **Problem:** The system currently tends to trash the full Fusion 360 project and restart from scratch because it cannot make surgical edits to specific features in the Fusion 360 timeline. When a feature fails (e.g., a cut in the wrong direction, a sketch at incorrect coordinates), the agent has no mechanism to edit or redefine that specific timeline operation. Instead, it creates duplicate features, accumulates failed operations in the timeline, or starts over entirely. In conversation `4017d2be`, the timeline grew to 124 entries with many no-op extrudes -- the agent could not go back and fix them. Priority: HIGH (P0).
- **Fix:** Implement timeline editing tools in [`fusion_addin/addin_server.py`](fusion_addin/addin_server.py): `edit_feature(timeline_index, new_params)` to modify an existing feature's parameters, `suppress_feature(timeline_index)` to disable a failed feature, `delete_feature(timeline_index)` to remove it, and `reorder_features(from_index, to_index)` for sequencing fixes. Add rules to [`config/rules/fusion_design_iteration.md`](config/rules/fusion_design_iteration.md) and [`docs/F360_SKILL.md`](docs/F360_SKILL.md): "When a feature produces zero volume change or unexpected results, suppress or delete it before attempting a corrected version -- do not leave dead features in the timeline."

#### [DONE] TASK-219: Sketch coordinate system varies silently per construction plane
- **Files:** [`docs/F360_SKILL.md`](docs/F360_SKILL.md), [`config/rules/fusion_design_iteration.md`](config/rules/fusion_design_iteration.md)
- **Problem:** When creating a sketch on an offset plane (e.g., XZ offset at Y=-0.5), sketch Y maps to world -Z (negated). The agent discovered this empirically after ~15 failed cut operations (~30 wasted tool calls). Should be documented in rules/skill system. Priority: HIGH.
- **Fix:** Add a coordinate mapping table to `F360_SKILL.md` documenting sketch-to-world axis mapping for each standard construction plane and offset variants. Add a rule in `fusion_design_iteration.md` requiring explicit coordinate verification after sketch creation on non-XY planes.

#### [DONE] TASK-220: Extrusion from coincident planes fails silently
- **Files:** [`docs/F360_SKILL.md`](docs/F360_SKILL.md), [`config/rules/fusion_design_iteration.md`](config/rules/fusion_design_iteration.md)
- **Problem:** Extruding a cut from a sketch on a plane coincident with a body face fails regardless of direction setting. Agent tried positive, negative, ThroughAll, symmetric -- all failed. Priority: HIGH.
- **Fix:** Add rule: "Never sketch on a plane coincident with a body face for cut operations; use an offset plane." Document in skill system with the empirical evidence. Consider adding a pre-extrude validation check in the addin that warns when the sketch plane is coincident with a body face.

#### [DONE] TASK-221: save_document has no save-as for new documents
- **Files:** [`fusion_addin/addin_server.py`](fusion_addin/addin_server.py), [`mcp/tool_groups.py`](mcp/tool_groups.py)
- **Problem:** Returns "Document has never been saved. Use File > Save As" with no programmatic workaround. Priority: MEDIUM.
- **Fix:** Implement a `save_as` tool that accepts a name parameter and calls `doc.saveAs()` with the provided name. Register in tool groups. This allows the agent to save new documents without requiring user interaction.

#### [DONE] TASK-222: rootComp alias forgotten across multi-step scripts
- **Files:** [`docs/F360_SKILL.md`](docs/F360_SKILL.md), [`ai/system_prompt.py`](ai/system_prompt.py)
- **Problem:** Scripts sometimes alias `root = rootComp` then subsequent scripts reference `root` without defining it, causing NameError. Each `execute_script` call gets a fresh namespace. Priority: LOW.
- **Fix:** Add rule to skill documentation: "Always use `rootComp` directly in scripts. Never rely on aliases from previous script executions -- each `execute_script` call has an isolated namespace." Add to system prompt verification protocol.

### Agent Loop & Iteration Management

#### [DONE] TASK-223: 50-iteration tool limit hit without advance warning
- **Files:** [`ai/claude_client.py`](ai/claude_client.py), [`config/settings.py`](config/settings.py)
- **Problem:** Agent hits max 50-tool-call limit (TASK-052) mid-design with no warning. The stop is abrupt, potentially leaving the design in an intermediate state. Priority: HIGH.
- **Fix:** At iteration 40 (configurable, default 80% of max), inject a system message warning the agent: "You have used 40 of 50 tool calls this turn. Plan a graceful stopping point -- save state, summarize progress, and note remaining steps." Make the warning threshold configurable in settings via `agent_iteration_warning_threshold`.

### Token/Cost Efficiency

#### [DONE] TASK-224: Excessive token burn from failed web research loops
- **Files:** [`ai/claude_client.py`](ai/claude_client.py), [`config/settings.py`](config/settings.py)
- **Problem:** When `web_search` returns empty, the agent retries 10-20 times before giving up. Should implement a "research budget" -- after 3-4 failed searches, ask the user for specs or fall back to internal knowledge with caveat. Priority: HIGH.
- **Fix:** Added `web_research_max_consecutive_failures` setting (default: 3). Track consecutive failed web searches per turn in the agent loop. After the limit, inject a system message: "Web research budget exhausted. Ask the user for the information directly or proceed with internal knowledge." Budget resets on successful web call or non-web tool call.

### Cross-Cutting Concerns

#### [DONE] TASK-225: (Meta) Add research-budget and tool-category-aware recovery patterns
- **Files:** [`ai/tool_recovery.py`](ai/tool_recovery.py), [`tests/test_tool_recovery.py`](tests/test_tool_recovery.py)
- **Problem:** Cross-cutting concern: the agent needs tool-category-aware recovery strategies. Web tools, CAD tools, and file tools should each have distinct failure/retry/fallback patterns rather than sharing generic CAD-oriented recovery advice. Priority: MEDIUM.
- **Fix:** Created `ai/tool_recovery.py` with centralized `get_recovery_strategy()` and `get_tool_category()` API. Defines tool categories (`web`, `cad`, `file`, `document`) imported from existing definitions. Each category has distinct budget thresholds, recovery suggestions, and system message injection rules. Web tools: after 3 failures suggest asking user, block retry. CAD tools: after 5 failures suggest diagnostics, never block. File/document tools: after 3 failures suggest path check. 50 unit tests in `tests/test_tool_recovery.py`. This is the umbrella module for TASK-216, TASK-217, and TASK-224.

---

## v1.10.0 -- Ollama Session Failure Analysis (convo_425)

Analysis of a failed Ollama session (`throwaway_folder/convo_425`, conversation `118bf7c9-a2b1-4576-a7eb-21b35eed98f1`) identified 13 systemic issues across tool registration, error handling, context management, API knowledge, and agent loop resilience. Model: qwen3.6:latest via Ollama, max_tokens=8100, 113 turns, 194 messages. Session exhibited catastrophic failure modes including 5x identical API error repetition, 3x full design rebuilds, 12+ calls to unregistered tools, and terminal loop collapse.

### Tool Registration & Validation

#### [x] TASK-226: Tool availability mismatch -- system prompt advertises unregistered tools
- **Files:** [`fusion_addin/addin_server.py`](fusion_addin/addin_server.py), [`fusion/bridge.py`](fusion/bridge.py), [`mcp/server.py`](mcp/server.py), [`tests/test_mcp_server.py`](tests/test_mcp_server.py), [`tests/test_fusion_bridge.py`](tests/test_fusion_bridge.py)
- **Problem:** The tool list shown to the LLM included `edit_feature`, `suppress_feature`, `delete_feature`, and `reorder_feature`, but when called they all returned `"Unknown command"`. The system prompt must only advertise tools that are actually registered in the running addin. Evidence: 12+ failed calls to these 4 tools wasting iteration budget. Related: TASK-218 added these tools but the addin version running did not have them. Priority: CRITICAL.
- **Fix:** Implemented three-layer defence: (1) Addin `list_commands` handler + bridge `query_available_commands()` enables dynamic tool list discovery at connection time; `MCPServer.validate_tool_availability()` cross-checks advertised vs addin tools, filtering `get_available_tools()` to exclude unavailable commands. (2) Runtime "Unknown command" detection in `MCPServer.execute_tool()` enhances the error response with clear guidance ("Do not retry this tool") and adds the tool to a session-level blocklist. (3) Blocklisted tools return a cached error immediately on retry without round-tripping to the addin, saving iteration budget.

### Error Detection & Recovery

#### [x] TASK-227: execute_script repetition detector bypass -- identical error patterns not caught
- **Files:** [`ai/repetition_detector.py`](ai/repetition_detector.py), [`ai/claude_client.py`](ai/claude_client.py)
- **Problem:** The repetition detector checks tool name + argument similarity, but `execute_script` calls always have unique script text, so the detector never fires even when the script contains the exact same broken API pattern (e.g., `body.areaProperties()` called 5 times across 5 different scripts, each failing with identical `AttributeError`). Evidence: `areaProperties` error repeated 5 times in same session; `surfaceType` string comparison error repeated 4 times. Priority: CRITICAL.
- **Fix:** Add script-level error pattern matching: when an `execute_script` call fails with a specific error signature (e.g., `AttributeError: 'BRepBody' object has no attribute 'areaProperties'`), cache that signature. If a subsequent `execute_script` call fails with the same error signature, escalate immediately (inject correction, block further calls with same pattern, or force a strategy change).
- **Done:** Added `ScriptErrorTracker` class to `ai/repetition_detector.py` that tracks (error_type, error_message) signatures from script errors. Warns after 2 repeats, blocks after 3. Includes `KNOWN_SCRIPT_ERROR_CORRECTIONS` dict for common Fusion API misuse (areaProperties, volumeProperties, faceCount, ValueInput). Integrated into `ai/claude_client.py` error enrichment pipeline. Adds `script_error_repeated`, `script_error_count`, `script_error_message` fields to tool results and sets `_force_stop` when blocked. Tests: `tests/test_script_error_tracker.py`.

#### [x] TASK-229: Inject error correction hints from diagnostic_data into LLM context
- **Files:** [`ai/error_classifier.py`](ai/error_classifier.py), [`ai/tool_recovery.py`](ai/tool_recovery.py), [`ai/claude_client.py`](ai/claude_client.py)
- **Problem:** Every `execute_script` error response already includes `diagnostic_data.body_list` with volumes and bounding boxes, but the LLM consistently ignored this data and instead tried to query it via script (which failed with the same API misuse). Evidence: LLM called `body.volumeProperties()`, `body.areaProperties()`, `body.faceCount` etc. repeatedly when all this data was already in the error response's diagnostic_data. Priority: HIGH.
- **Fix:** The error_classifier or tool_recovery system should parse diagnostic_data and inject a short summary into the next system message: e.g., "NOTE: Body 'Box' volume=680.4 cm3, bbox=(0,0,0)-(20,12,13). Use get_body_properties tool instead of scripting volume queries."
- **Done:** Added `format_diagnostic_summary()` to `ai/tool_recovery.py` that extracts a compact `[DESIGN STATE]` string from `diagnostic_data` (body_list with volumes/bounding boxes, sketch_info with profiles/curves, body_properties with volume/area/face_count). Integrated into `ai/claude_client.py` -- when `diagnostic_data` is set on a failed tool result, the summary is injected as `result["diagnostic_summary"]` before the result is serialized to the LLM. Handles missing fields, invalid types, and multiple data sections gracefully. Tests: `tests/test_diagnostic_summary.py` (28 tests).

#### [x] TASK-230: Detect rebuild-from-scratch loops
- **Files:** [`ai/repetition_detector.py`](ai/repetition_detector.py), [`ai/claude_client.py`](ai/claude_client.py)
- **Problem:** The LLM called `new_document` 3 times as a "start fresh" strategy, each time rebuilding ~20 features only to hit the same fundamental error. The repetition detector should track `new_document` calls and flag when 2+ occur in the same conversation. Evidence: 3 full rebuilds, ~60 wasted tool calls, identical failure each time. Priority: HIGH.
- **Fix:** After the second `new_document`, inject a system message: "WARNING: You have restarted the design N times. Identify the root cause before rebuilding. Previous attempts failed because: [summarize errors]."
- **Done:** Added `RebuildLoopDetector` class to `ai/repetition_detector.py` that tracks `new_document` calls per conversation. After 2nd call injects `[WARNING]` with error summary from `ScriptErrorTracker.get_stats()`. After 3rd+ call escalates to `[CRITICAL]` advising the LLM to ask the user for help. Integrated into `ai/claude_client.py` tool loop -- warning injected into tool result dict as `rebuild_warning`. Resets on conversation clear via `_reset_state()`. Tests: `tests/test_rebuild_loop_detector.py` (26 tests).

#### [x] TASK-236: Empty assistant response detection and recovery
- **Files:** [`ai/claude_client.py`](ai/claude_client.py), [`ai/providers/ollama_provider.py`](ai/providers/ollama_provider.py)
- **Problem:** The conversation ends with `"content": []` -- an empty assistant response. The agent loop should detect empty responses and handle them gracefully. Evidence: conversation terminated silently with no final output. Priority: MEDIUM.
- **Fix:** Detect empty responses and either: (a) retry with a nudge message, (b) inject a "please continue" system message, or (c) gracefully terminate with a summary of progress made so far.
- **Done:** Added empty response detection in the agent loop in `ai/claude_client.py`. Detects `[]`, `""`, `None`, and lists with no text/tool_use blocks. First empty triggers retry with a nudge message. Second consecutive empty terminates gracefully with a design state summary. Counter resets on non-empty responses. Tests in `tests/test_agent_loop.py`.

### Context Window & Token Management

#### [x] TASK-228: Context window size guard for complex tasks
- **Files:** [`ai/context_window_guard.py`](ai/context_window_guard.py), [`ai/claude_client.py`](ai/claude_client.py), [`web/events.py`](web/events.py), [`tests/test_context_window_guard.py`](tests/test_context_window_guard.py)
- **Problem:** The Ollama session used max_tokens=8100, which is catastrophically small for a multi-step parametric CAD design with 35+ parameters and 12+ build steps. Evidence: LLM forgot coordinate mapping rules, repeated same errors, lost track of build sequence. Priority: HIGH.
- **Fix:** Implemented `ContextWindowGuard` with: (a) `check_adequacy()` that estimates minimum context needed based on tool count, system prompt size, and configurable thresholds -- returns ok/warning/critical levels; (b) adequacy check at conversation start that emits `context_window_warning` events to the UI and injects a conciseness system message for critical contexts; (c) runtime `check_pressure()` after each API response that emits `context_pressure` events at 80% usage and injects a pressure system message at 90%; (d) all thresholds are configurable via `ContextWindowThresholds` dataclass.

#### [x] TASK-237: Script error deduplication in conversation history
- **Files:** [`ai/claude_client.py`](ai/claude_client.py), [`fusion_addin/addin_server.py`](fusion_addin/addin_server.py)
- **Problem:** When an `execute_script` call fails, the full traceback appears in both `stderr` and `error` fields of the response, plus a `diagnostic_data` block with the body list. This triples the token cost of error responses. Evidence: each script error consumed ~500-1000 tokens of duplicated content across 3 fields, compounding the context window exhaustion. Priority: LOW.
- **Fix:** Deduplicate: only include the traceback once, and fold `diagnostic_data` into a compact summary (e.g., "3 bodies, main=706cm3").

### Fusion 360 API Knowledge Base

#### [x] TASK-231: Boolean/Combine API patterns missing from Fusion API knowledge base
- **Files:** [`docs/F360_SKILL.md`](docs/F360_SKILL.md), [`ai/system_prompt.py`](ai/system_prompt.py)
- **Problem:** The LLM could not figure out the correct signature for `CombineFeatures.createInput()` (requires `ObjectCollection` for tool bodies, not a single `BRepBody`). TASK-156 added Fusion API patterns to the system prompt, but boolean combine operations were not included. Evidence: combine API failed at line ~3058 of conversation. Priority: HIGH.
- **Fix:** Add patterns for: `combineFeatures.createInput(targetBody, toolBodiesCollection)`, `ObjectCollection.createWithArray()`, `FeatureOperations.CutFeatureOperation` vs `CombineFeatures` cut.
- **Done:** Added "Boolean Combine Operations" section to F360_SKILL.md Appendix E with full API signature, ObjectCollection requirement, operation table, and practical example.

#### [x] TASK-232: surfaceType enum comparison pattern
- **Files:** [`docs/F360_SKILL.md`](docs/F360_SKILL.md), [`ai/system_prompt.py`](ai/system_prompt.py)
- **Problem:** The LLM repeatedly compared `face.geometry.surfaceType` using string comparison (`str(geo.surfaceType) == 'adsk::core::SurfaceTypes::PlanarSurfaceType'`) which always evaluates to False because `surfaceType` returns an integer enum, not a string. Evidence: 4 failed face-type checks causing all faces to report as "Other/NonPlanar", preventing the LLM from finding the front face for sketching. Priority: HIGH.
- **Fix:** Add to Fusion API patterns: "surfaceType is an integer enum. Compare with `adsk.core.SurfaceTypes.PlaneSurfaceType` (note: PlaneSurfaceType, not PlanarSurfaceType)."
- **Done:** Added "Surface Type Checking" section to F360_SKILL.md Appendix E with correct/wrong examples, all enum values, and PlaneSurfaceType vs PlanarSurfaceType warning.

#### [x] TASK-233: create_box position parameter documentation
- **Files:** [`fusion_addin/addin_server.py`](fusion_addin/addin_server.py), [`mcp/server.py`](mcp/server.py), [`docs/F360_SKILL.md`](docs/F360_SKILL.md)
- **Problem:** The LLM repeatedly confused whether the `position` parameter of `create_box` is the center or minimum corner. First attempt placed box at (10,6,0) expecting center semantics but got min-corner, resulting in bbox (10,6,0)-(30,18,13) instead of (0,0,0)-(20,12,13). Evidence: box created at wrong position, had to delete and recreate. Priority: MEDIUM.
- **Fix:** The tool description should explicitly state: "position is the minimum corner (origin) of the box, not the center." The response should also include the resulting bounding box.
- **Done:** Updated tool description in mcp/server.py and corrected F360_SKILL.md section 4.1 (was incorrectly documenting center-point semantics; now matches actual addTwoPointRectangle implementation).

### Agent Loop & Iteration Management

#### [x] TASK-234: Track "meaningful progress" vs "thrashing" in iteration budget
- **Files:** [`ai/claude_client.py`](ai/claude_client.py), [`ai/progress_tracker.py`](ai/progress_tracker.py)
- **Problem:** The 50-tool-call limit (TASK-052) treats all calls equally, but there is a significant difference between productive calls (create geometry, apply materials) and thrashing calls (undo, delete_body, new_document, failed execute_script). Evidence: 113 turns, ~50+ tool calls, only ~30% produced lasting geometry. Priority: MEDIUM.
- **Fix:** Track a "progress score" based on net bodies added, volume changes, and timeline advancement. Warn earlier when thrashing ratio exceeds a threshold (e.g., >60% of calls are undos/deletes/failures).
- **Done:** Created `ai/progress_tracker.py` with `ProgressTracker` class that categorises each tool call as productive, thrashing, neutral, or restart. Tracks counters and computes thrashing ratio. When `thrashing_ratio > 0.6` AND `total_calls > 10`, emits a `[THRASHING WARNING]` into the conversation. `execute_script` is classified by success/failure. Integrated into the agent loop in `ai/claude_client.py` after each tool execution. Resets per turn. Tests: `tests/test_progress_tracker.py` (30+ tests), integration tests in `tests/test_agent_loop.py`.

### Ollama Provider

#### [x] TASK-235: Ollama model capability profiling and warnings
- **Files:** [`ai/providers/ollama_provider.py`](ai/providers/ollama_provider.py), [`config/settings.py`](config/settings.py)
- **Problem:** The session used `qwen3.6:latest` with 8100 max tokens. The system has no awareness of model capabilities or limitations. Evidence: model forgot instructions repeatedly, generated incorrect API calls, entered loops. Priority: MEDIUM.
- **Fix:** Maintain a capability profile for known models (context window, tool-calling reliability, code generation quality) and warn when a model is likely insufficient for the requested task complexity. For Ollama models, query the model's actual context window via `/api/show` and display it in the UI alongside the user's max_tokens setting.

### Post-Session Analysis

#### [x] TASK-238: Post-session failure analysis report
- **Files:** [`ai/conversation_manager.py`](ai/conversation_manager.py), [`ai/claude_client.py`](ai/claude_client.py)
- **Problem:** When a conversation hits the iteration limit or ends with repeated failures, there is no automated analysis of what went wrong. Diagnosing issues requires manual review of the full conversation JSON. Evidence: this entire analysis task -- all 13 issues above were discovered only through manual inspection. Priority: LOW.
- **Fix:** Auto-generate a failure analysis report summarizing: (a) unique errors encountered, (b) repeated error patterns, (c) tools that were advertised but unavailable, (d) rebuild count, (e) net geometry progress. Store alongside the conversation JSON. This would have surfaced the issues in convo_425 immediately rather than requiring manual review.

> **Source:** Analysis of failed Ollama session `throwaway_folder/convo_425` and
> `data/conversations/118bf7c9-a2b1-4576-a7eb-21b35eed98f1.json`.
> Model: qwen3.6:latest via Ollama, max_tokens=8100, 113 turns, 194 messages.
> Session exhibited: 5x identical API error repetition, 3x full design rebuilds,
> 12+ calls to unregistered tools, coordinate mapping amnesia, and terminal loop collapse.

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
