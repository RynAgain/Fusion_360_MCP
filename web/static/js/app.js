/* ==========================================================================
   Artifex360 -- Main Application JS
   Vanilla JS + Socket.IO + marked.js
   ========================================================================== */

// --------------------------------------------------------------------------
// Socket.IO Connection
// --------------------------------------------------------------------------
const socket = io({ transports: ['websocket', 'polling'] });

// --------------------------------------------------------------------------
// Application State
// --------------------------------------------------------------------------
const state = {
  isThinking: false,
  currentAiEl: null,       // DOM element currently being streamed into
  currentAiText: '',       // accumulated raw text for the current AI msg
  settingsPanelOpen: false,
  toolsPanelOpen: true,
  conversationsPanelOpen: false,
  docSelectorOpen: false,
  modeSelectorOpen: false,
  timelineExpanded: true,
  fusionConnected: false,
  simulationMode: false,
  requireConfirmation: false,
  toolsCount: 0,
  statusPollId: null,
  activeMode: 'full',
  activeProvider: 'anthropic',
};

// --------------------------------------------------------------------------
// DOM References
// --------------------------------------------------------------------------
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const dom = {
  // Layout
  loadingOverlay:   $('#loading-overlay'),
  toolsPanel:       $('#tools-panel'),
  toolsList:        $('#tools-list'),
  settingsPanel:    $('#settings-panel'),
  chatMessages:     $('#chat-messages'),
  messageInput:     $('#message-input'),

  // Buttons
  sendBtn:          $('#send-btn'),
  cancelBtn:        $('#cancel-btn'),
  clearBtn:         $('#clear-btn'),
  settingsBtn:      $('#settings-btn'),
  toolsBtn:         $('#tools-btn'),
  connIndicator:    $('#conn-indicator'),
  connLabel:        $('#conn-label'),

  // Theme toggle
  themeToggle:      $('#themeToggle'),
  themeIcon:        $('#themeIcon'),

  // Conversations
  conversationsBtn:   $('#conversations-btn'),
  conversationsPanel: $('#conversationsPanel'),
  conversationsList:  $('#conversationsList'),
  convNewBtn:         $('#conv-new-btn'),
  convSaveBtn:        $('#conv-save-btn'),

  // Document selector
  docSelectorBtn:    $('#docSelectorBtn'),
  docSelectorPanel:  $('#docSelectorPanel'),
  docList:           $('#docList'),
  activeDocName:     $('#activeDocName'),
  newDocBtn:         $('#newDocBtn'),

  // Mode selector
  modeSelectorBtn:    $('#modeSelectorBtn'),
  modeSelectorPanel:  $('#modeSelectorPanel'),
  modeList:           $('#modeList'),
  activeModeName:     $('#activeModeName'),

  // Task plan
  taskPlanSection:    $('#taskPlanSection'),
  taskPlanList:       $('#taskPlanList'),
  taskPlanProgress:   $('#taskPlanProgress'),
  clearTasksBtn:      $('#clearTasksBtn'),

  // Timeline
  timelineHeader:    $('#timeline-header'),
  timelineContainer: $('#timelineContainer'),
  timelineList:      $('#timelineList'),
  timelineEmpty:     $('#timelineEmpty'),
  refreshTimeline:   $('#refreshTimeline'),

  // Token usage
  tokenUsage:       $('#tokenUsage'),

  // Confirmation modal
  confirmModal:     $('#confirmModal'),
  confirmToolName:  $('#confirmToolName'),
  confirmToolArgs:  $('#confirmToolArgs'),
  confirmDismissBtn: $('#confirmDismissBtn'),

  // Status bar
  statusPill:       $('#status-pill'),
  statusPillLabel:  $('#status-pill-label'),
  statusLog:        $('#status-log'),

  // Settings form
  settApiKey:       $('#sett-api-key'),
  settApiKeyToggle: $('#sett-api-key-toggle'),
  settModel:        $('#sett-model'),
  settMaxTokens:    $('#sett-max-tokens'),
  settMaxTokensVal: $('#sett-max-tokens-val'),
  settSystemPrompt: $('#sett-system-prompt'),
  settSimulation:   $('#sett-simulation'),
  settConfirmation: $('#sett-confirmation'),
  settMaxRpm:       $('#sett-max-rpm'),
  saveSettingsBtn:  $('#save-settings-btn'),
};

// --------------------------------------------------------------------------
// Marked.js Configuration
// --------------------------------------------------------------------------
if (typeof marked !== 'undefined') {
  marked.setOptions({
    breaks: true,
    gfm: true,
    headerIds: false,
    mangle: false,
  });
}

// --------------------------------------------------------------------------
// Utility Helpers
// --------------------------------------------------------------------------

/** Render markdown text to HTML using marked.js or a basic fallback. */
function renderMarkdown(text) {
  if (typeof marked !== 'undefined') {
    return marked.parse(text);
  }
  // Basic fallback
  return text
    .replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/\n/g, '<br>');
}

/** Format a timestamp for display. */
function timeStr(date) {
  if (!date) date = new Date();
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

/** Escape HTML special characters. */
function esc(str) {
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

/** Truncate string with ellipsis. */
function truncate(str, len) {
  if (!str) return '';
  return str.length > len ? str.slice(0, len) + '...' : str;
}

/** Scroll chat to bottom. */
function scrollToBottom() {
  requestAnimationFrame(() => {
    dom.chatMessages.scrollTop = dom.chatMessages.scrollHeight;
  });
}

/** Show a toast notification. */
function showToast(message, type) {
  type = type || 'success';
  let toast = document.querySelector('.toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.className = 'toast';
    document.body.appendChild(toast);
  }
  toast.textContent = message;
  toast.className = 'toast ' + type;
  // Force reflow
  void toast.offsetWidth;
  toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), 2500);
}

/** Pretty-print JSON for tool card bodies. */
function prettyJSON(obj) {
  try {
    if (typeof obj === 'string') obj = JSON.parse(obj);
    return JSON.stringify(obj, null, 2);
  } catch (_e) {
    return String(obj);
  }
}

// --------------------------------------------------------------------------
// SVG Icons (inline to avoid external dependency)
// --------------------------------------------------------------------------
const ICONS = {
  send: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>',
  stop: '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><rect x="4" y="4" width="16" height="16" rx="2"/></svg>',
  clear: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
  settings: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
  tools: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>',
  wrench: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>',
  check: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>',
  x: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
  chevron: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>',
  eye: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>',
  eyeOff: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>',
  image: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>',
  shield: '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>',
  download: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>',
  trash: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
  moon: '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
  sun: '<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>',
};

// Destructive tools that require confirmation
const DESTRUCTIVE_TOOLS = [
  'undo', 'redo', 'save_document', 'execute_script',
  'export_stl', 'export_step', 'export_f3d',
  'close_document',
];

// Geometry tools that should trigger timeline refresh
const GEO_TOOLS = [
  'create_cylinder', 'create_box', 'create_sphere', 'extrude', 'revolve',
  'add_fillet', 'add_chamfer', 'mirror_body', 'undo', 'redo',
  'create_sketch', 'boolean_operation', 'apply_material', 'set_parameter',
];

// Document tools that should trigger document list refresh
const DOC_TOOLS = [
  'new_document', 'close_document', 'switch_document', 'save_document',
];

// --------------------------------------------------------------------------
// Theme Switching (Feature 1)
// --------------------------------------------------------------------------

/** Load theme from localStorage and apply it. Called early before overlay hides. */
function loadTheme() {
  const saved = localStorage.getItem('theme') || 'dark';
  document.documentElement.setAttribute('data-theme', saved);
  updateThemeIcon(saved);
}

/** Toggle between dark and light themes. */
function toggleTheme() {
  const html = document.documentElement;
  const current = html.getAttribute('data-theme') || 'dark';
  const next = current === 'dark' ? 'light' : 'dark';
  html.setAttribute('data-theme', next);
  updateThemeIcon(next);

  // Save preference locally
  localStorage.setItem('theme', next);

  // Also save to server settings
  fetch('/api/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ theme: next }),
  }).catch(() => {});
}

/** Update the theme toggle icon to reflect the current theme. */
function updateThemeIcon(theme) {
  const icon = dom.themeIcon;
  if (!icon) return;
  if (theme === 'dark') {
    icon.innerHTML = ICONS.moon;
  } else {
    icon.innerHTML = ICONS.sun;
  }
}

// --------------------------------------------------------------------------
// Chat: Message Rendering
// --------------------------------------------------------------------------

/** Add a user message bubble. */
function addUserMessage(text) {
  const row = document.createElement('div');
  row.className = 'msg-row user';

  const bubble = document.createElement('div');
  bubble.className = 'msg-bubble user';
  bubble.innerHTML = renderMarkdown(text);

  row.appendChild(bubble);
  dom.chatMessages.appendChild(row);
  scrollToBottom();
}

/** Start a new AI message bubble (for streaming). Returns the bubble element. */
function startAiMessage() {
  const row = document.createElement('div');
  row.className = 'msg-row ai';

  const bubble = document.createElement('div');
  bubble.className = 'msg-bubble ai';
  bubble.innerHTML = '';

  row.appendChild(bubble);
  dom.chatMessages.appendChild(row);
  scrollToBottom();

  state.currentAiEl = bubble;
  state.currentAiText = '';
  return bubble;
}

/** Append streaming text to the current AI message. */
function appendToAiMessage(text) {
  if (!state.currentAiEl) startAiMessage();
  state.currentAiText += text;
  state.currentAiEl.innerHTML = renderMarkdown(state.currentAiText);
  scrollToBottom();
}

/** Finalize the current AI message. */
function finalizeAiMessage(fullText) {
  if (!state.currentAiEl) return;
  if (fullText) {
    state.currentAiText = fullText;
    state.currentAiEl.innerHTML = renderMarkdown(fullText);
  }
  state.currentAiEl = null;
  state.currentAiText = '';
  scrollToBottom();
}

/** Add a tool call card. */
function addToolCall(data) {
  const toolName = data.tool_name || 'unknown';
  const isDestructive = DESTRUCTIVE_TOOLS.includes(toolName);
  const needsConfirmBadge = state.requireConfirmation && isDestructive;

  const card = document.createElement('div');
  card.className = 'tool-card call' + (needsConfirmBadge ? ' confirmation-required' : '');
  card.dataset.toolUseId = data.tool_use_id || '';

  const badgeHtml = needsConfirmBadge
    ? '<span class="tool-card-confirmation-badge">' + ICONS.shield + ' Confirmed</span>'
    : '';

  const header = document.createElement('div');
  header.className = 'tool-card-header';
  header.innerHTML =
    '<span class="tool-card-icon">' + ICONS.wrench + '</span>' +
    '<span class="tool-card-name">' + esc(toolName) + '</span>' +
    badgeHtml +
    '<span class="tool-card-label">Calling...</span>' +
    '<span class="tool-card-chevron">' + ICONS.chevron + '</span>';

  const body = document.createElement('div');
  body.className = 'tool-card-body';
  body.innerHTML = '<pre>' + esc(prettyJSON(data.arguments || {})) + '</pre>';

  header.addEventListener('click', () => {
    body.classList.toggle('open');
    header.querySelector('.tool-card-chevron').classList.toggle('open');
  });

  card.appendChild(header);
  card.appendChild(body);

  const row = document.createElement('div');
  row.className = 'msg-row ai';
  row.appendChild(card);
  dom.chatMessages.appendChild(row);
  scrollToBottom();

  // Show confirmation modal for destructive tools
  if (needsConfirmBadge) {
    showConfirmModal(toolName, data.arguments || {});
  }
}

/** Add a tool result card. */
function addToolResult(data) {
  const isError = data.result && (data.result.status === 'error' || data.result.error);
  const card = document.createElement('div');
  card.className = 'tool-card result' + (isError ? ' error-result' : '');

  const icon = isError ? ICONS.x : ICONS.check;
  const labelText = isError ? 'Error' : 'Result';

  const header = document.createElement('div');
  header.className = 'tool-card-header';
  header.innerHTML =
    '<span class="tool-card-icon">' + icon + '</span>' +
    '<span class="tool-card-name">' + esc(data.tool_name || 'unknown') + '</span>' +
    '<span class="tool-card-label">' + labelText + '</span>' +
    '<span class="tool-card-chevron">' + ICONS.chevron + '</span>';

  const body = document.createElement('div');
  body.className = 'tool-card-body';
  body.innerHTML = '<pre>' + esc(prettyJSON(data.result || {})) + '</pre>';

  header.addEventListener('click', () => {
    body.classList.toggle('open');
    header.querySelector('.tool-card-chevron').classList.toggle('open');
  });

  card.appendChild(header);
  card.appendChild(body);

  const row = document.createElement('div');
  row.className = 'msg-row ai';
  row.appendChild(card);
  dom.chatMessages.appendChild(row);
  scrollToBottom();
}

/** Add an error message in chat. */
function addErrorMessage(text) {
  const row = document.createElement('div');
  row.className = 'msg-row ai';

  const bubble = document.createElement('div');
  bubble.className = 'msg-bubble error';
  bubble.textContent = text;

  row.appendChild(bubble);
  dom.chatMessages.appendChild(row);
  scrollToBottom();
}

/** Add a system message (centered, italic). */
function addSystemMessage(text) {
  const row = document.createElement('div');
  row.className = 'msg-row system';

  const bubble = document.createElement('div');
  bubble.className = 'msg-bubble system';
  bubble.textContent = text;

  row.appendChild(bubble);
  dom.chatMessages.appendChild(row);
  scrollToBottom();
}

/** Add a screenshot to the chat. */
function addScreenshot(base64, format) {
  const row = document.createElement('div');
  row.className = 'msg-row ai';

  const wrapper = document.createElement('div');
  wrapper.className = 'msg-bubble ai';

  const label = document.createElement('div');
  label.innerHTML = '<span style="display:inline-flex;align-items:center;gap:4px;margin-bottom:6px;font-size:0.78rem;color:var(--text-secondary);">' + ICONS.image + ' Viewport Screenshot</span>';

  const img = document.createElement('img');
  img.className = 'msg-screenshot';
  img.src = 'data:image/' + (format || 'png') + ';base64,' + base64;
  img.alt = 'Fusion 360 viewport';
  img.addEventListener('click', () => {
    window.open(img.src, '_blank');
  });

  wrapper.appendChild(label);
  wrapper.appendChild(img);
  row.appendChild(wrapper);
  dom.chatMessages.appendChild(row);
  scrollToBottom();
}

// --------------------------------------------------------------------------
// Thinking Indicator
// --------------------------------------------------------------------------

function showThinking() {
  // Remove any existing indicator first
  hideThinking();

  const row = document.createElement('div');
  row.className = 'msg-row ai';
  row.id = 'thinking-row';

  const indicator = document.createElement('div');
  indicator.className = 'thinking-indicator';
  indicator.innerHTML = '<div class="dot"></div><div class="dot"></div><div class="dot"></div>';

  row.appendChild(indicator);
  dom.chatMessages.appendChild(row);
  scrollToBottom();
}

function hideThinking() {
  const existing = document.getElementById('thinking-row');
  if (existing) existing.remove();
}

// --------------------------------------------------------------------------
// Input State Management
// --------------------------------------------------------------------------

function setThinking(val) {
  state.isThinking = val;
  dom.messageInput.disabled = val;
  dom.sendBtn.disabled = val;
  dom.sendBtn.style.display = val ? 'none' : '';
  dom.cancelBtn.style.display = val ? '' : 'none';

  if (val) {
    showThinking();
  } else {
    hideThinking();
  }
}

// --------------------------------------------------------------------------
// Send Message
// --------------------------------------------------------------------------

function sendMessage() {
  const text = dom.messageInput.value.trim();
  if (!text || state.isThinking) return;

  addUserMessage(text);
  dom.messageInput.value = '';
  autoGrowTextarea();

  socket.emit('user_message', { message: text });
}

// --------------------------------------------------------------------------
// Socket.IO Event Handlers
// --------------------------------------------------------------------------

socket.on('connect', () => {
  addStatusLog('WebSocket connected');
});

socket.on('disconnect', () => {
  addStatusLog('WebSocket disconnected');
  setThinking(false);
});

socket.on('text_delta', (data) => {
  if (!state.currentAiEl) startAiMessage();
  appendToAiMessage(data.text || '');
});

socket.on('text_done', (data) => {
  finalizeAiMessage(data.full_text || null);
});

socket.on('tool_call', (data) => {
  // Finalize any pending AI text before showing tool card
  if (state.currentAiEl) finalizeAiMessage();
  addToolCall(data);
});

socket.on('tool_result', (data) => {
  addToolResult(data);
  // Dismiss confirmation modal if still showing
  dismissConfirmModal();

  // Auto-refresh timeline after geometry operations
  if (data.tool_name && GEO_TOOLS.includes(data.tool_name)) {
    setTimeout(refreshTimeline, 500);
  }

  // Auto-refresh document list after document operations
  if (data.tool_name && DOC_TOOLS.includes(data.tool_name)) {
    setTimeout(refreshDocuments, 500);
  }
});

socket.on('error', (data) => {
  addErrorMessage(data.message || 'An unknown error occurred.');
});

socket.on('done', () => {
  if (state.currentAiEl) finalizeAiMessage();
  setThinking(false);
});

// Feature 2: Token usage display
socket.on('token_usage', (data) => {
  updateTokenDisplay(data);
});

// Feature 1: Conversation saved notification
socket.on('conversation_saved', (data) => {
  showToast('Conversation saved: ' + (data.title || 'Untitled'), 'success');
  // Refresh list if panel is open
  if (state.conversationsPanelOpen) {
    loadConversationsList();
  }
});

socket.on('thinking_start', () => {
  setThinking(true);
});

socket.on('thinking_stop', () => {
  hideThinking();
});

socket.on('status_update', (data) => {
  addStatusLog(data.message || '');

  // Update connection state if included
  if (typeof data.fusion_connected !== 'undefined') {
    state.fusionConnected = data.fusion_connected;
  }
  if (typeof data.simulation_mode !== 'undefined') {
    state.simulationMode = data.simulation_mode;
  }
  if (typeof data.tools_count !== 'undefined') {
    state.toolsCount = data.tools_count;
  }
  updateConnectionUI();
});

socket.on('screenshot', (data) => {
  if (data.image_base64) {
    addScreenshot(data.image_base64, data.format || 'png');
  }
});

// --------------------------------------------------------------------------
// Connection UI
// --------------------------------------------------------------------------

function updateConnectionUI() {
  const ind = dom.connIndicator;
  const label = dom.connLabel;
  const pill = dom.statusPill;
  const pillLabel = dom.statusPillLabel;

  // Top bar indicator
  ind.classList.remove('connected', 'disconnected', 'simulation');
  pill.classList.remove('ok', 'err', 'sim');

  if (state.simulationMode) {
    ind.classList.add('simulation');
    label.textContent = 'Simulation';
    pill.classList.add('sim');
    pillLabel.textContent = 'SIM';
  } else if (state.fusionConnected) {
    ind.classList.add('connected');
    label.textContent = 'Connected';
    pill.classList.add('ok');
    pillLabel.textContent = 'LIVE';
  } else {
    ind.classList.add('disconnected');
    label.textContent = 'Disconnected';
    pill.classList.add('err');
    pillLabel.textContent = 'OFF';
  }
}

function toggleConnection() {
  if (state.fusionConnected && !state.simulationMode) {
    // Disconnect
    fetch('/api/disconnect', { method: 'POST' })
      .then(r => r.json())
      .then(() => {
        state.fusionConnected = false;
        updateConnectionUI();
        addStatusLog('Disconnected from Fusion 360');
        refreshDocuments();
      })
      .catch(err => addStatusLog('Disconnect error: ' + err.message));
  } else {
    // Connect
    fetch('/api/connect', { method: 'POST' })
      .then(r => r.json())
      .then(data => {
        state.fusionConnected = !data.simulation_mode;
        state.simulationMode = data.simulation_mode || false;
        updateConnectionUI();
        addStatusLog(data.message || 'Connected');
        refreshDocuments();
      })
      .catch(err => addStatusLog('Connect error: ' + err.message));
  }
}

// --------------------------------------------------------------------------
// Status Bar
// --------------------------------------------------------------------------

function addStatusLog(msg) {
  if (!msg) return;
  const ts = timeStr();
  dom.statusLog.textContent = ts + ' -- ' + msg;
}

// --------------------------------------------------------------------------
// Status Polling
// --------------------------------------------------------------------------

function loadStatus() {
  return fetch('/api/status')
    .then(r => r.json())
    .then(data => {
      state.fusionConnected = data.fusion_connected;
      state.simulationMode = data.simulation_mode;
      state.toolsCount = data.tools_count;
      updateConnectionUI();
    })
    .catch(() => {});
}

// --------------------------------------------------------------------------
// Tools Sidebar
// --------------------------------------------------------------------------

function loadTools() {
  return fetch('/api/tools')
    .then(r => r.json())
    .then(data => {
      var tools;
      if (Array.isArray(data)) {
        tools = data;
      } else {
        tools = data.tools || [];
      }
      renderToolsList(tools);

      // Show filter info if in a restricted mode
      if (data.mode && data.mode !== 'full' && data.total) {
        var info = document.getElementById('tools-filter-info');
        if (!info) {
          info = document.createElement('div');
          info.id = 'tools-filter-info';
          info.className = 'tools-filter-info';
          dom.toolsList.parentNode.insertBefore(info, dom.toolsList);
        }
        info.textContent = data.filtered + ' of ' + data.total + ' tools (mode: ' + data.mode + ')';
        info.style.display = 'block';
      } else {
        var info = document.getElementById('tools-filter-info');
        if (info) info.style.display = 'none';
      }
    })
    .catch(() => {
      dom.toolsList.innerHTML = '<div style="padding:0.75rem;color:var(--text-secondary);font-size:0.78rem;">Could not load tools.</div>';
    });
}

function renderToolsList(tools) {
  // Group by category
  const cats = {};
  tools.forEach(t => {
    const cat = t.category || 'General';
    if (!cats[cat]) cats[cat] = [];
    cats[cat].push(t);
  });

  let html = '';
  const catOrder = ['Document', 'Geometry', 'Edit', 'General'];
  const sortedCats = Object.keys(cats).sort((a, b) => {
    const ia = catOrder.indexOf(a);
    const ib = catOrder.indexOf(b);
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
  });

  sortedCats.forEach(cat => {
    html += '<div class="tool-category-header">' + esc(cat) + '</div>';
    cats[cat].forEach(t => {
      html +=
        '<div class="tool-item">' +
          '<div class="tool-item-name">' + esc(t.name) + '</div>' +
          '<div class="tool-item-desc">' + esc(truncate(t.description, 100)) + '</div>' +
        '</div>';
    });
  });

  dom.toolsList.innerHTML = html;
}

function toggleTools() {
  state.toolsPanelOpen = !state.toolsPanelOpen;
  dom.toolsPanel.classList.toggle('collapsed', !state.toolsPanelOpen);
  dom.toolsBtn.classList.toggle('active', state.toolsPanelOpen);
}

// --------------------------------------------------------------------------
// Design History Timeline (Feature 2)
// --------------------------------------------------------------------------

/** Toggle timeline container visibility. */
function toggleTimeline() {
  state.timelineExpanded = !state.timelineExpanded;
  dom.timelineContainer.classList.toggle('collapsed', !state.timelineExpanded);
}

/** Fetch timeline data from the server and render it. */
async function refreshTimeline() {
  try {
    const res = await fetch('/api/timeline');
    const data = await res.json();
    renderTimeline(data.timeline || []);
  } catch (e) {
    console.error('Failed to load timeline:', e);
  }
}

/** Render timeline items into the sidebar panel. */
function renderTimeline(items) {
  const list = dom.timelineList;
  const empty = dom.timelineEmpty;

  if (!items || items.length === 0) {
    list.innerHTML = '';
    empty.style.display = 'block';
    return;
  }

  empty.style.display = 'none';
  list.innerHTML = items.map(function(item, i) {
    const isSuppressed = item.is_suppressed || item.is_rolled_back;
    const isCurrent = (i === items.length - 1) && !isSuppressed;
    const cls = 'timeline-item' +
      (isSuppressed ? ' suppressed' : '') +
      (isCurrent ? ' current' : '');
    return '<div class="' + cls + '">' +
      '<span class="timeline-index">' + (item.index != null ? item.index : i) + '</span>' +
      '<span class="timeline-name">' + esc(item.name || 'Feature') + '</span>' +
      '<span class="timeline-type">' + esc(item.type || '') + '</span>' +
    '</div>';
  }).join('');
}

// --------------------------------------------------------------------------
// Document Management (multi-document support)
// --------------------------------------------------------------------------

/** Toggle the document selector dropdown. */
function toggleDocSelector() {
  if (state.docSelectorOpen) {
    closeDocSelector();
  } else {
    openDocSelector();
  }
}

/** Open the document selector panel and refresh the list. */
function openDocSelector() {
  state.docSelectorOpen = true;
  dom.docSelectorPanel.classList.remove('hidden');
  dom.docSelectorBtn.classList.add('open');
  refreshDocuments();
}

/** Close the document selector panel. */
function closeDocSelector() {
  state.docSelectorOpen = false;
  dom.docSelectorPanel.classList.add('hidden');
  dom.docSelectorBtn.classList.remove('open');
}

/** Fetch document list from the server and render it. */
async function refreshDocuments() {
  try {
    const res = await fetch('/api/documents');
    const data = await res.json();
    renderDocumentList(data.documents || []);

    // Update active doc name in top bar
    const activeDoc = data.active_document || 'No Document';
    dom.activeDocName.textContent = activeDoc;
  } catch (e) {
    console.error('Failed to load documents:', e);
  }
}

/** Render document items into the dropdown list. */
function renderDocumentList(docs) {
  const list = dom.docList;

  if (!docs || docs.length === 0) {
    list.innerHTML = '<div class="doc-list-empty">No open documents</div>';
    return;
  }

  let html = '';
  docs.forEach(function(doc) {
    const isActive = doc.is_active;
    const cls = 'doc-item' + (isActive ? ' active' : '');
    const statusCls = doc.is_saved ? 'saved' : 'unsaved';
    const statusTitle = doc.is_saved ? 'Saved' : 'Unsaved changes';

    html +=
      '<div class="' + cls + '">' +
        '<div class="doc-item-status ' + statusCls + '" title="' + statusTitle + '"></div>' +
        '<div class="doc-item-info">' +
          '<div class="doc-item-name">' + esc(doc.name) + '</div>' +
          '<div class="doc-item-meta">' +
            '<span>' + esc(doc.data_file || 'Untitled') + '</span>' +
            (doc.version ? '<span>v' + doc.version + '</span>' : '') +
          '</div>' +
        '</div>' +
        (isActive
          ? '<span class="doc-item-active-badge">Active</span>'
          : '<div class="doc-item-actions">' +
              '<button class="doc-item-btn switch-btn" title="Switch to this document" onclick="switchDocument(\'' + esc(doc.name).replace(/'/g, "\\'") + '\')">' +
                ICONS.chevron +
              '</button>' +
              '<button class="doc-item-btn close-btn" title="Close document" onclick="closeDocument(\'' + esc(doc.name).replace(/'/g, "\\'") + '\')">' +
                ICONS.x +
              '</button>' +
            '</div>'
        ) +
      '</div>';
  });

  list.innerHTML = html;
}

/** Switch the active document by name. */
async function switchDocument(name) {
  try {
    const res = await fetch('/api/documents/switch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ document_name: name }),
    });
    const data = await res.json();
    if (data.success || data.status === 'simulation') {
      showToast('Switched to ' + (data.active_document || name), 'success');
      await refreshDocuments();
      refreshTimeline();
    } else {
      showToast(data.error || 'Failed to switch document', 'error');
    }
  } catch (e) {
    showToast('Switch failed: ' + e.message, 'error');
  }
}

/** Create a new document. */
async function newDocument() {
  try {
    const res = await fetch('/api/documents/new', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    const data = await res.json();
    if (data.success || data.status === 'simulation') {
      showToast(data.message || 'New document created', 'success');
      await refreshDocuments();
      refreshTimeline();
    } else {
      showToast(data.error || 'Failed to create document', 'error');
    }
  } catch (e) {
    showToast('Create failed: ' + e.message, 'error');
  }
}

/** Close a document by name. */
async function closeDocument(name) {
  if (!confirm('Close "' + name + '"? Unsaved changes will be saved first.')) return;
  try {
    const res = await fetch('/api/documents/close', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ document_name: name, save: true }),
    });
    const data = await res.json();
    if (data.success || data.status === 'simulation') {
      showToast(data.message || 'Document closed', 'success');
      await refreshDocuments();
      refreshTimeline();
    } else {
      showToast(data.error || 'Failed to close document', 'error');
    }
  } catch (e) {
    showToast('Close failed: ' + e.message, 'error');
  }
}

// --------------------------------------------------------------------------
// Mode Management
// --------------------------------------------------------------------------

/** Load modes from the server and render the selector. */
async function loadModes() {
  try {
    const res = await fetch('/api/modes');
    const data = await res.json();
    renderModeSelector(data.modes || [], data.active || 'full');
    state.activeMode = data.active || 'full';
  } catch (e) {
    console.error('Failed to load modes:', e);
  }
}

/** Render mode items in the selector panel. */
function renderModeSelector(modes, activeSlug) {
  const list = dom.modeList;
  if (!list) return;

  list.innerHTML = modes.map(function(m) {
    var cls = 'mode-item' + (m.slug === activeSlug ? ' active' : '');
    return '<div class="' + cls + '" onclick="switchMode(\'' + esc(m.slug) + '\')">' +
      '<div class="mode-item-name">' + esc(m.name) + '</div>' +
      '<div class="mode-item-tools">' + m.tool_count + ' tools</div>' +
    '</div>';
  }).join('');

  if (dom.activeModeName) {
    var active = modes.find(function(m) { return m.slug === activeSlug; });
    dom.activeModeName.textContent = active ? active.name : 'Full Access';
  }
}

/** Switch to a different CAD mode. */
async function switchMode(slug) {
  try {
    const res = await fetch('/api/modes/' + slug, { method: 'POST' });
    const data = await res.json();
    if (data.success) {
      state.activeMode = slug;
      loadModes();
      loadTools(); // Refresh tools since available set changed
      loadTasks(); // Refresh tasks
      showToast('Switched to ' + data.mode.name, 'success');
    } else {
      showToast(data.error || 'Failed to switch mode', 'error');
    }
  } catch (e) {
    showToast('Mode switch failed: ' + e.message, 'error');
  }
  closeModeSelector();
}

/** Toggle the mode selector panel. */
function toggleModeSelector() {
  if (state.modeSelectorOpen) {
    closeModeSelector();
  } else {
    openModeSelector();
  }
}

/** Open the mode selector panel. */
function openModeSelector() {
  state.modeSelectorOpen = true;
  if (dom.modeSelectorPanel) dom.modeSelectorPanel.classList.remove('hidden');
  if (dom.modeSelectorBtn) dom.modeSelectorBtn.classList.add('open');
  loadModes();
}

/** Close the mode selector panel. */
function closeModeSelector() {
  state.modeSelectorOpen = false;
  if (dom.modeSelectorPanel) dom.modeSelectorPanel.classList.add('hidden');
  if (dom.modeSelectorBtn) dom.modeSelectorBtn.classList.remove('open');
}

// --------------------------------------------------------------------------
// Task / Design Plan Management
// --------------------------------------------------------------------------

/** Load the current design plan from the server. */
async function loadTasks() {
  try {
    const res = await fetch('/api/tasks');
    const data = await res.json();
    renderTaskPlan(data);
  } catch (e) {
    console.error('Failed to load tasks:', e);
  }
}

/** Render the task plan into the sidebar. */
function renderTaskPlan(data) {
  var section = dom.taskPlanSection;
  if (!section) return;

  if (!data.tasks || data.tasks.length === 0) {
    section.style.display = 'none';
    return;
  }
  section.style.display = 'block';

  var list = dom.taskPlanList;
  list.innerHTML = data.tasks.map(function(t) {
    var icon;
    if (t.status === 'completed') icon = ICONS.check;
    else if (t.status === 'in_progress') icon = '<span style="color:var(--accent);">[-]</span>';
    else if (t.status === 'failed') icon = ICONS.x;
    else icon = '<span style="opacity:0.4;">[ ]</span>';

    var cls = 'task-item ' + t.status;
    return '<div class="' + cls + '">' +
      '<span class="task-status">' + icon + '</span>' +
      '<span class="task-desc">' + esc(t.description) + '</span>' +
    '</div>';
  }).join('');

  var prog = data.progress || {};
  if (dom.taskPlanProgress) {
    dom.taskPlanProgress.textContent =
      (prog.completed || 0) + '/' + (prog.total || 0) + ' complete';
  }
}

/** Clear all tasks from the design plan. */
async function clearTasks() {
  try {
    await fetch('/api/tasks', { method: 'DELETE' });
    loadTasks();
    showToast('Design plan cleared', 'success');
  } catch (e) {
    showToast('Clear failed: ' + e.message, 'error');
  }
}

// --------------------------------------------------------------------------
// Settings Panel
// --------------------------------------------------------------------------

function toggleSettings() {
  state.settingsPanelOpen = !state.settingsPanelOpen;
  dom.settingsPanel.classList.toggle('open', state.settingsPanelOpen);
  dom.settingsPanel.classList.toggle('closed', !state.settingsPanelOpen);
  dom.settingsBtn.classList.toggle('active', state.settingsPanelOpen);
}

function loadSettings() {
  return fetch('/api/settings')
    .then(r => r.json())
    .then(data => {
      // Populate form fields
      dom.settApiKey.value = data.anthropic_api_key || '';
      // Store desired model as pending; refreshAnthropicModels() will select it
      dom.settModel.dataset.pending = data.model || 'claude-sonnet-4-20250514';
      dom.settMaxTokens.value = data.max_tokens || 4096;
      dom.settMaxTokensVal.textContent = data.max_tokens || 4096;
      dom.settSystemPrompt.value = data.system_prompt || '';
      dom.settSimulation.checked = !!data.fusion_simulation_mode;
      dom.settConfirmation.checked = !!data.require_confirmation;
      dom.settMaxRpm.value = data.max_requests_per_minute || 10;
      // Track confirmation state
      state.requireConfirmation = !!data.require_confirmation;

      // Provider settings
      if (data.provider) {
        state.activeProvider = data.provider;
        selectProvider(data.provider, /* save */ false);
      }
      var ollamaUrl = document.getElementById('ollamaBaseUrl');
      if (ollamaUrl && data.ollama_base_url) {
        ollamaUrl.value = data.ollama_base_url;
      }
      var ollamaModel = document.getElementById('ollamaModel');
      if (ollamaModel && data.ollama_model) {
        // Set value if present; actual option list populated by refreshOllamaModels
        ollamaModel.dataset.pending = data.ollama_model;
      }

      // Apply theme from server if no local override
      if (!localStorage.getItem('theme') && data.theme) {
        document.documentElement.setAttribute('data-theme', data.theme);
        updateThemeIcon(data.theme);
      }

      // Populate Anthropic models dropdown after settings are loaded
      refreshAnthropicModels();
    })
    .catch(() => {
      addStatusLog('Could not load settings');
    });
}

function saveSettings() {
  const payload = {};

  // Provider
  payload.provider = state.activeProvider;

  if (state.activeProvider === 'anthropic') {
    // Only include API key if it was changed (not the masked value)
    const apiKey = dom.settApiKey.value.trim();
    if (apiKey && !apiKey.includes('***') && !apiKey.includes('...')) {
      payload.anthropic_api_key = apiKey;
    }
    payload.model = dom.settModel.value;
  } else if (state.activeProvider === 'ollama') {
    var ollamaUrl = document.getElementById('ollamaBaseUrl');
    var ollamaModel = document.getElementById('ollamaModel');
    if (ollamaUrl) payload.ollama_base_url = ollamaUrl.value;
    if (ollamaModel && ollamaModel.value) payload.ollama_model = ollamaModel.value;
  }

  payload.max_tokens = parseInt(dom.settMaxTokens.value, 10);
  payload.system_prompt = dom.settSystemPrompt.value;
  payload.fusion_simulation_mode = dom.settSimulation.checked;
  payload.require_confirmation = dom.settConfirmation.checked;
  payload.max_requests_per_minute = parseInt(dom.settMaxRpm.value, 10) || 10;

  dom.saveSettingsBtn.disabled = true;

  fetch('/api/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
    .then(r => r.json())
    .then(data => {
      showToast('Settings saved', 'success');
      // Update simulation state
      state.simulationMode = !!data.fusion_simulation_mode;
      state.requireConfirmation = !!data.require_confirmation;
      updateConnectionUI();
    })
    .catch(err => {
      showToast('Failed to save: ' + err.message, 'error');
    })
    .finally(() => {
      dom.saveSettingsBtn.disabled = false;
    });
}

function toggleApiKeyVisibility() {
  const isPassword = dom.settApiKey.type === 'password';
  dom.settApiKey.type = isPassword ? 'text' : 'password';
  dom.settApiKeyToggle.innerHTML = isPassword ? ICONS.eyeOff : ICONS.eye;
}

// --------------------------------------------------------------------------
// LLM Provider Management
// --------------------------------------------------------------------------

/** Switch the visible provider settings panel. */
function selectProvider(type, save) {
  state.activeProvider = type;

  // Update tab active states
  document.querySelectorAll('.provider-tab').forEach(function(tab) {
    tab.classList.toggle('active', tab.dataset.provider === type);
  });

  // Toggle settings panels
  var anthropicEl = document.getElementById('anthropicSettings');
  var ollamaEl = document.getElementById('ollamaSettings');
  if (anthropicEl) anthropicEl.style.display = (type === 'anthropic') ? '' : 'none';
  if (ollamaEl) ollamaEl.style.display = (type === 'ollama') ? '' : 'none';

  // When switching to Ollama, check status and load models
  if (type === 'ollama') {
    checkOllamaStatus();
    refreshOllamaModels();
  }

  // When switching to Anthropic, refresh model list
  if (type === 'anthropic') {
    refreshAnthropicModels();
  }

  // Optionally save the switch to the backend (skip during initial load)
  if (save !== false) {
    fetch('/api/providers/' + type, { method: 'POST' }).catch(function() {});
  }
}

/** Check if Ollama is running and update the status indicator. */
async function checkOllamaStatus() {
  var el = document.getElementById('ollamaStatus');
  if (!el) return;
  try {
    var res = await fetch('/api/providers/ollama/status');
    var data = await res.json();
    if (data.available) {
      el.textContent = 'Connected to Ollama';
      el.className = 'provider-status connected';
    } else {
      el.textContent = 'Ollama not running. Start it with: ollama serve';
      el.className = 'provider-status disconnected';
    }
  } catch (e) {
    el.textContent = 'Cannot reach Ollama';
    el.className = 'provider-status disconnected';
  }
}

/** Fetch available models from the Anthropic provider and populate the select. */
async function refreshAnthropicModels() {
  var select = dom.settModel;
  if (!select) return;
  try {
    var res = await fetch('/api/providers/anthropic/models');
    var data = await res.json();
    var models = data.models || [];
    if (models.length === 0) {
      select.innerHTML = '<option value="">No models available</option>';
      return;
    }
    select.innerHTML = '';
    models.forEach(function(m) {
      var opt = document.createElement('option');
      opt.value = m.id;
      opt.textContent = m.name || m.id;
      select.appendChild(opt);
    });
    // Restore currently selected model from settings if known
    if (select.dataset.pending) {
      select.value = select.dataset.pending;
      delete select.dataset.pending;
    }
  } catch (e) {
    console.error('Failed to load Anthropic models:', e);
    select.innerHTML = '<option value="">Failed to load models</option>';
  }
}

/** Fetch available models from the Ollama instance and populate the select. */
async function refreshOllamaModels() {
  var select = document.getElementById('ollamaModel');
  if (!select) return;
  var pending = select.dataset.pending || '';
  try {
    var res = await fetch('/api/providers/ollama/models');
    var data = await res.json();
    select.innerHTML = '<option value="">-- Select model --</option>';
    (data.models || []).forEach(function(m) {
      var opt = document.createElement('option');
      opt.value = m.id;
      opt.textContent = m.name;
      if (pending && m.id === pending) opt.selected = true;
      select.appendChild(opt);
    });
    delete select.dataset.pending;
  } catch (e) {
    select.innerHTML = '<option value="">Failed to load models</option>';
  }
}

/** Load provider state from the server (called during init). */
async function loadProviders() {
  try {
    var res = await fetch('/api/providers');
    var data = await res.json();
    state.activeProvider = data.active || 'anthropic';
    selectProvider(data.active || 'anthropic', /* save */ false);
  } catch (e) {
    console.error('Failed to load providers:', e);
  }
}

// --------------------------------------------------------------------------
// Input Area: Auto-grow & Keyboard
// --------------------------------------------------------------------------

function autoGrowTextarea() {
  const el = dom.messageInput;
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 144) + 'px'; // 144px ~ 6 lines
}

function handleInputKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
}

// --------------------------------------------------------------------------
// Clear History
// --------------------------------------------------------------------------

function clearHistory() {
  socket.emit('clear_history', {});
  dom.chatMessages.innerHTML = '';
  addSystemMessage('Conversation cleared');
  state.currentAiEl = null;
  state.currentAiText = '';
  resetTokenDisplay();
}

// --------------------------------------------------------------------------
// Cancel
// --------------------------------------------------------------------------

function cancelRequest() {
  socket.emit('cancel', {});
  addStatusLog('Cancel requested');
}

// --------------------------------------------------------------------------
// Feature 1: Conversation Management
// --------------------------------------------------------------------------

/** Toggle the conversations dropdown panel. */
function toggleConversations() {
  if (state.conversationsPanelOpen) {
    closeConversations();
  } else {
    openConversations();
  }
}

/** Open the conversations panel and refresh the list. */
function openConversations() {
  state.conversationsPanelOpen = true;
  dom.conversationsPanel.classList.remove('hidden');
  dom.conversationsBtn.classList.add('active');
  loadConversationsList();
}

/** Close the conversations panel. */
function closeConversations() {
  state.conversationsPanelOpen = false;
  dom.conversationsPanel.classList.add('hidden');
  dom.conversationsBtn.classList.remove('active');
}

/** Fetch and render the conversation list from the API. */
function loadConversationsList() {
  fetch('/api/conversations')
    .then(r => r.json())
    .then(conversations => {
      if (!Array.isArray(conversations) || conversations.length === 0) {
        dom.conversationsList.innerHTML = '<div class="conversations-empty">No saved conversations</div>';
        return;
      }
      // Sort by updated_at descending
      conversations.sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));

      let html = '';
      conversations.forEach(conv => {
        html +=
          '<div class="conv-item" data-id="' + esc(conv.id) + '">' +
            '<div class="conv-item-info">' +
              '<div class="conv-item-title">' + esc(truncate(conv.title || 'Untitled', 45)) + '</div>' +
              '<div class="conv-item-meta">' +
                '<span>' + formatRelativeTime(conv.updated_at) + '</span>' +
                '<span>' + (conv.message_count || 0) + ' messages</span>' +
              '</div>' +
            '</div>' +
            '<div class="conv-item-actions">' +
              '<button class="conv-item-btn load-btn" title="Load conversation" onclick="loadConversation(\'' + esc(conv.id) + '\')">' +
                ICONS.download +
              '</button>' +
              '<button class="conv-item-btn delete-btn" title="Delete conversation" onclick="deleteConversation(\'' + esc(conv.id) + '\')">' +
                ICONS.trash +
              '</button>' +
            '</div>' +
          '</div>';
      });
      dom.conversationsList.innerHTML = html;
    })
    .catch(() => {
      dom.conversationsList.innerHTML = '<div class="conversations-empty">Failed to load conversations</div>';
    });
}

/** Save the current conversation. */
function saveConversation() {
  dom.convSaveBtn.disabled = true;
  fetch('/api/conversations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  })
    .then(r => r.json())
    .then(meta => {
      showToast('Conversation saved', 'success');
      loadConversationsList();
    })
    .catch(err => {
      showToast('Save failed: ' + err.message, 'error');
    })
    .finally(() => {
      dom.convSaveBtn.disabled = false;
    });
}

/** Load a conversation by ID. */
function loadConversation(id) {
  addStatusLog('Loading conversation...');

  // Step 1: POST to load into backend
  fetch('/api/conversations/' + id + '/load', { method: 'POST' })
    .then(r => r.json())
    .then(loadResult => {
      if (loadResult.error) {
        showToast('Load failed: ' + loadResult.error, 'error');
        return Promise.reject(new Error(loadResult.error));
      }
      // Step 2: GET the full conversation with messages
      return fetch('/api/conversations/' + id).then(r => r.json());
    })
    .then(data => {
      if (!data || data.error) {
        showToast('Failed to fetch conversation', 'error');
        return;
      }
      // Step 3: Render the loaded conversation
      dom.chatMessages.innerHTML = '';
      resetTokenDisplay();
      renderConversationHistory(data.messages || []);
      addStatusLog('Loaded conversation: ' + (data.title || 'Untitled'));
      closeConversations();
    })
    .catch(err => {
      addStatusLog('Load error: ' + (err.message || err));
    });
}

/** Delete a conversation by ID with confirmation. */
function deleteConversation(id) {
  if (!confirm('Delete this conversation? This cannot be undone.')) return;

  fetch('/api/conversations/' + id, { method: 'DELETE' })
    .then(r => r.json())
    .then(() => {
      showToast('Conversation deleted', 'success');
      loadConversationsList();
    })
    .catch(err => {
      showToast('Delete failed: ' + err.message, 'error');
    });
}

/** Render a loaded conversation's messages in the chat area. */
function renderConversationHistory(messages) {
  if (!messages || !Array.isArray(messages)) return;

  messages.forEach(msg => {
    const role = msg.role;

    if (role === 'user') {
      // User messages can have string content or array of content blocks
      const text = typeof msg.content === 'string'
        ? msg.content
        : (msg.content || []).filter(b => b.type === 'text').map(b => b.text).join('\n');
      if (text) addUserMessage(text);

    } else if (role === 'assistant') {
      const content = msg.content;
      if (typeof content === 'string') {
        // Simple text response
        const row = document.createElement('div');
        row.className = 'msg-row ai';
        const bubble = document.createElement('div');
        bubble.className = 'msg-bubble ai';
        bubble.innerHTML = renderMarkdown(content);
        row.appendChild(bubble);
        dom.chatMessages.appendChild(row);
      } else if (Array.isArray(content)) {
        // Process content blocks
        let textAccum = '';
        content.forEach(block => {
          if (block.type === 'text') {
            textAccum += (textAccum ? '\n' : '') + block.text;
          } else if (block.type === 'tool_use') {
            // Flush any accumulated text first
            if (textAccum) {
              const row = document.createElement('div');
              row.className = 'msg-row ai';
              const bubble = document.createElement('div');
              bubble.className = 'msg-bubble ai';
              bubble.innerHTML = renderMarkdown(textAccum);
              row.appendChild(bubble);
              dom.chatMessages.appendChild(row);
              textAccum = '';
            }
            addToolCall({
              tool_use_id: block.id,
              tool_name: block.name,
              arguments: block.input,
            });
          }
        });
        // Flush remaining text
        if (textAccum) {
          const row = document.createElement('div');
          row.className = 'msg-row ai';
          const bubble = document.createElement('div');
          bubble.className = 'msg-bubble ai';
          bubble.innerHTML = renderMarkdown(textAccum);
          row.appendChild(bubble);
          dom.chatMessages.appendChild(row);
        }
      }

    } else if (role === 'tool') {
      // Tool result message -- rendered as a result card
      // The content may be a string or an array of content blocks
      let resultContent = msg.content;
      if (typeof resultContent === 'string') {
        try { resultContent = JSON.parse(resultContent); } catch (_) {}
      } else if (Array.isArray(resultContent)) {
        // Extract text from content blocks
        const textParts = resultContent.filter(b => b.type === 'text').map(b => b.text);
        if (textParts.length === 1) {
          try { resultContent = JSON.parse(textParts[0]); } catch (_) { resultContent = textParts[0]; }
        } else {
          resultContent = textParts.join('\n');
        }
      }
      addToolResult({
        tool_use_id: msg.tool_use_id || '',
        tool_name: msg.tool_name || 'tool',
        result: resultContent,
      });
    }
  });

  scrollToBottom();
}

/** Format an ISO date string into a relative time string. */
function formatRelativeTime(isoString) {
  if (!isoString) return '';
  const date = new Date(isoString);
  const now = new Date();
  const diffMs = now - date;
  const diffSec = Math.floor(diffMs / 1000);
  const diffMin = Math.floor(diffSec / 60);
  const diffHr = Math.floor(diffMin / 60);
  const diffDay = Math.floor(diffHr / 24);

  if (diffSec < 60) return 'just now';
  if (diffMin < 60) return diffMin + ' min ago';
  if (diffHr < 24) return diffHr + ' hr ago';
  if (diffDay === 1) return 'yesterday';
  if (diffDay < 7) return diffDay + ' days ago';
  return date.toLocaleDateString();
}

// --------------------------------------------------------------------------
// Feature 2: Token Usage Display
// --------------------------------------------------------------------------

/** Update the token usage display in the status bar. */
function updateTokenDisplay(data) {
  const el = dom.tokenUsage;
  if (!el) return;
  const fmt = (n) => (n || 0).toLocaleString();
  el.textContent = 'Tokens: ' + fmt(data.input_tokens) + ' in / ' + fmt(data.output_tokens) + ' out' +
    ' (total: ' + fmt(data.total_input_tokens) + ' in / ' + fmt(data.total_output_tokens) + ' out)' +
    ' | Turns: ' + (data.turn_count || 0);
}

/** Reset the token usage display. */
function resetTokenDisplay() {
  if (dom.tokenUsage) {
    dom.tokenUsage.textContent = '';
  }
}

// --------------------------------------------------------------------------
// Feature 3: Confirmation Modal for Destructive Operations
// --------------------------------------------------------------------------

/**
 * Show the confirmation modal for a destructive tool call.
 * TASK-031: Returns a Promise that resolves to true (Allow) or false (Deny).
 */
function showConfirmModal(toolName, args) {
  return new Promise(function(resolve) {
    dom.confirmToolName.textContent = toolName;
    dom.confirmToolArgs.textContent = prettyJSON(args);
    dom.confirmModal.classList.remove('hidden');

    // Store the resolver so the button handlers can call it
    dom.confirmModal._resolve = resolve;
  });
}

/** Handle "Allow" click on the confirmation modal. */
function handleConfirmAllow() {
  var resolve = dom.confirmModal._resolve;
  dom.confirmModal.classList.add('hidden');
  dom.confirmModal._resolve = null;
  socket.emit('tool_confirmation', { allowed: true });
  if (resolve) resolve(true);
}

/** Handle "Deny" click on the confirmation modal. */
function handleConfirmDeny() {
  var resolve = dom.confirmModal._resolve;
  dom.confirmModal.classList.add('hidden');
  dom.confirmModal._resolve = null;
  socket.emit('tool_confirmation', { allowed: false });
  if (resolve) resolve(false);
}

/** Dismiss / close the confirmation modal (treated as deny). */
function dismissConfirmModal() {
  var resolve = dom.confirmModal._resolve;
  dom.confirmModal.classList.add('hidden');
  dom.confirmModal._resolve = null;
  if (resolve) resolve(false);
}

// TASK-031: Listen for confirm_tool events from the server
socket.on('confirm_tool', function(data) {
  showConfirmModal(data.tool_name || 'unknown', data.arguments || {});
});

// --------------------------------------------------------------------------
// Event Binding
// --------------------------------------------------------------------------

function bindEvents() {
  // Send / Cancel / Clear
  dom.sendBtn.addEventListener('click', sendMessage);
  dom.cancelBtn.addEventListener('click', cancelRequest);
  dom.clearBtn.addEventListener('click', clearHistory);

  // Input
  dom.messageInput.addEventListener('input', autoGrowTextarea);
  dom.messageInput.addEventListener('keydown', handleInputKeydown);

  // Top bar
  dom.settingsBtn.addEventListener('click', toggleSettings);
  dom.toolsBtn.addEventListener('click', toggleTools);
  dom.connIndicator.addEventListener('click', toggleConnection);

  // Theme toggle
  if (dom.themeToggle) {
    dom.themeToggle.addEventListener('click', toggleTheme);
  }

  // Timeline
  if (dom.timelineHeader) {
    dom.timelineHeader.addEventListener('click', function(e) {
      // Don't toggle if clicking the refresh button
      if (e.target.closest('.sidebar-action-btn')) return;
      toggleTimeline();
    });
  }
  if (dom.refreshTimeline) {
    dom.refreshTimeline.addEventListener('click', function(e) {
      e.stopPropagation();
      refreshTimeline();
    });
  }

  // Conversations panel
  dom.conversationsBtn.addEventListener('click', toggleConversations);
  dom.convNewBtn.addEventListener('click', () => {
    clearHistory();
    closeConversations();
  });
  dom.convSaveBtn.addEventListener('click', saveConversation);

  // Confirmation modal
  dom.confirmDismissBtn.addEventListener('click', dismissConfirmModal);
  dom.confirmModal.querySelector('.confirm-modal-backdrop').addEventListener('click', dismissConfirmModal);
  // TASK-031: Allow / Deny buttons
  var allowBtn = document.getElementById('confirmAllowBtn');
  var denyBtn = document.getElementById('confirmDenyBtn');
  if (allowBtn) allowBtn.addEventListener('click', handleConfirmAllow);
  if (denyBtn) denyBtn.addEventListener('click', handleConfirmDeny);

  // Document selector
  dom.docSelectorBtn.addEventListener('click', toggleDocSelector);
  dom.newDocBtn.addEventListener('click', newDocument);

  // Mode selector
  if (dom.modeSelectorBtn) {
    dom.modeSelectorBtn.addEventListener('click', toggleModeSelector);
  }

  // Task plan clear button
  if (dom.clearTasksBtn) {
    dom.clearTasksBtn.addEventListener('click', clearTasks);
  }

  // Close conversations panel, doc selector, and mode selector on outside click
  document.addEventListener('click', (e) => {
    if (state.conversationsPanelOpen &&
        !dom.conversationsPanel.contains(e.target) &&
        !dom.conversationsBtn.contains(e.target)) {
      closeConversations();
    }
    if (state.docSelectorOpen &&
        !dom.docSelectorPanel.contains(e.target) &&
        !dom.docSelectorBtn.contains(e.target)) {
      closeDocSelector();
    }
    if (state.modeSelectorOpen &&
        dom.modeSelectorPanel && dom.modeSelectorBtn &&
        !dom.modeSelectorPanel.contains(e.target) &&
        !dom.modeSelectorBtn.contains(e.target)) {
      closeModeSelector();
    }
  });

  // Settings
  dom.saveSettingsBtn.addEventListener('click', saveSettings);
  dom.settApiKeyToggle.addEventListener('click', toggleApiKeyVisibility);
  dom.settMaxTokens.addEventListener('input', () => {
    dom.settMaxTokensVal.textContent = dom.settMaxTokens.value;
  });
}

// --------------------------------------------------------------------------
// Initialization
// --------------------------------------------------------------------------

function init() {
  // Apply theme ASAP (before overlay removal) to prevent flash
  loadTheme();

  bindEvents();

  // Load initial data in parallel
  Promise.all([
    loadSettings(),
    loadTools(),
    loadStatus(),
    loadModes(),
    loadTasks(),
    loadProviders(),
  ]).finally(() => {
    // Hide loading overlay
    if (dom.loadingOverlay) {
      dom.loadingOverlay.classList.add('hidden');
      setTimeout(() => dom.loadingOverlay.remove(), 500);
    }
    addSystemMessage('Artifex360 ready. Type a message to begin.');

    // Initial timeline and document list load
    refreshTimeline();
    refreshDocuments();
  });

  // Poll status every 10 seconds
  state.statusPollId = setInterval(loadStatus, 10000);
}

// Start when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
