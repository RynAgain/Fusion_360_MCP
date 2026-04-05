"""
ui/app.py
Main application window — wires together ChatPanel, SettingsPanel, StatusPanel,
ClaudeClient, MCPServer, and FusionBridge.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import logging
import os
import shutil
import sys

from config.settings import settings
from fusion.bridge import FusionBridge
from mcp.server import MCPServer
from ai.claude_client import ClaudeClient, EventType
from ui.chat_panel import ChatPanel
from ui.settings_panel import SettingsPanel
from ui.status_panel import StatusPanel

logger = logging.getLogger(__name__)

APP_TITLE   = "Fusion 360 MCP Controller"
MIN_WIDTH   = 900
MIN_HEIGHT  = 600

ADDIN_SRC   = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fusion_addin")
ADDIN_DEST  = os.path.expanduser(
    "~/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns/Fusion360MCP"
)

COLORS = {
    "bg":      "#1e1e2e",
    "surface": "#2a2a3e",
    "border":  "#3b3b5c",
    "accent":  "#7aa2f7",
    "accent2": "#bb9af7",
    "success": "#9ece6a",
    "warning": "#e0af68",
    "error":   "#f7768e",
    "text":    "#c0caf5",
    "text_dim":"#565f89",
}


class App(tk.Tk):
    """
    Root Tk window.
    Layout:
        ┌─────────────────────────────────────────┐
        │  Toolbar                                │
        ├──────────────────────┬──────────────────┤
        │                      │                  │
        │   ChatPanel          │  SettingsPanel   │
        │   (left, ~65%)       │  (right, ~35%)   │
        │                      │                  │
        ├──────────────────────┴──────────────────┤
        │  StatusPanel (bottom log strip)         │
        └─────────────────────────────────────────┘
    """

    def __init__(self):
        super().__init__()

        # ── Window setup ────────────────────────────────────────────────
        self.title(APP_TITLE)
        self.configure(bg=COLORS["bg"])
        w = settings.get("window_width",  1200)
        h = settings.get("window_height", 800)
        self.geometry(f"{w}x{h}")
        self.minsize(MIN_WIDTH, MIN_HEIGHT)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        try:
            self.createcommand("tk::mac::Quit", self._on_close)
        except Exception:
            pass

        # ── Style ────────────────────────────────────────────────────────
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TPanedwindow", background=COLORS["bg"])
        style.configure(
            "Vertical.TScrollbar",
            background=COLORS["surface"],
            troughcolor=COLORS["bg"],
            bordercolor=COLORS["border"],
            arrowcolor=COLORS["accent"],
        )

        # ── Backend wiring ───────────────────────────────────────────────
        # Bridge tries to connect to the Fusion add-in; falls back to sim
        self._bridge = FusionBridge(simulation_mode=settings.simulation_mode)
        self._mcp    = MCPServer(self._bridge)
        self._claude = ClaudeClient(settings, self._mcp)

        # MCP hooks
        if settings.require_confirmation:
            self._mcp.add_pre_hook(self._confirm_tool)
        self._mcp.add_post_hook(self._on_tool_result_hook)

        # ── Build UI ─────────────────────────────────────────────────────
        self._build_toolbar()
        self._build_main_area()
        self._build_status_bar()

        # ── Initial connection (deferred so window is visible first) ─────
        self.after(300, self._init_connection)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_toolbar(self):
        bar = tk.Frame(self, bg=COLORS["surface"], pady=6)
        bar.pack(fill=tk.X)

        tk.Label(
            bar,
            text="⚡  " + APP_TITLE,
            bg=COLORS["surface"],
            fg=COLORS["accent"],
            font=("SF Pro Display", 15, "bold"),
            padx=14,
        ).pack(side=tk.LEFT)

        # Connection status pill
        self._conn_label = tk.Label(
            bar,
            text="◌  Connecting…",
            bg=COLORS["surface"],
            fg=COLORS["warning"],
            font=("SF Pro Text", 11),
            padx=10,
        )
        self._conn_label.pack(side=tk.LEFT, padx=8)

        # Right-side toolbar buttons
        buttons = [
            ("🔄  Reconnect",    self._reconnect),
            ("🗑  Clear Chat",   self._clear_chat),
            ("📋  Tools",        self._show_tools),
            ("📦  Install Add-in", self._install_addin),
        ]
        for label, cmd in reversed(buttons):
            tk.Button(
                bar,
                text=label,
                command=cmd,
                bg=COLORS["surface"],
                fg=COLORS["text"],
                font=("SF Pro Text", 11),
                relief=tk.FLAT,
                padx=10,
                pady=4,
                cursor="hand2",
                activebackground=COLORS["border"],
                activeforeground=COLORS["accent"],
            ).pack(side=tk.RIGHT, padx=4)

    def _build_main_area(self):
        self._paned = tk.PanedWindow(
            self,
            orient=tk.HORIZONTAL,
            bg=COLORS["border"],
            sashwidth=4,
            sashrelief=tk.FLAT,
            handlesize=0,
        )
        self._paned.pack(fill=tk.BOTH, expand=True)

        self._chat = ChatPanel(self._paned, on_send=self._on_user_send)
        self._paned.add(self._chat, stretch="always", minsize=400)

        self._settings_panel = SettingsPanel(
            self._paned,
            settings=settings,
            on_save=self._on_settings_saved,
        )
        self._paned.add(self._settings_panel, stretch="never", minsize=280)

        self.after(150, lambda: self._paned.sash_place(0, int(self.winfo_width() * 0.65), 0))

    def _build_status_bar(self):
        tk.Frame(self, bg=COLORS["border"], height=1).pack(fill=tk.X)
        self._status = StatusPanel(self)
        self._status.pack(fill=tk.X, side=tk.BOTTOM)

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _init_connection(self):
        result = self._bridge.connect()
        sim    = self._bridge.simulation_mode

        self._status.set_fusion_status(connected=(result["status"] != "error"), simulation=sim)

        if sim:
            self._set_conn_label("◌  Simulation Mode", COLORS["warning"])
            self._status.log_warning("Fusion 360 add-in not detected — running in simulation mode.")
            self._status.log_info(result.get("message", ""))
        elif result["status"] == "success":
            self._set_conn_label("●  Fusion 360 Connected", COLORS["success"])
            self._status.log_success("Connected to Fusion 360 add-in.")
        else:
            self._set_conn_label("✕  Connection Failed", COLORS["error"])
            self._status.log_error(f"Fusion 360 connection failed: {result['message']}")

        self._status.log_system(
            f"MCP server ready — {len(self._mcp.get_tool_names())} tools registered."
        )
        self._status.log_system(f"Model: {settings.model}  |  Max tokens: {settings.max_tokens}")

    def _set_conn_label(self, text: str, color: str):
        self._conn_label.configure(text=text, fg=color)

    # ------------------------------------------------------------------
    # User message handling
    # ------------------------------------------------------------------

    def _on_user_send(self, text: str):
        self._chat.add_user_message(text)
        self._chat.set_thinking(True)
        self._status.log_info(f"User: {text[:80]}{'…' if len(text) > 80 else ''}")
        self._current_ai_tag = None
        self._claude.send_message(text, on_event=self._on_claude_event)

    def _on_claude_event(self, event_type: str, payload: dict):
        if event_type == EventType.TEXT_DELTA:
            if self._current_ai_tag is None:
                self._current_ai_tag = self._chat.start_ai_message()
            self._chat.append_ai_text(payload["text"], self._current_ai_tag)

        elif event_type == EventType.TEXT_DONE:
            if self._current_ai_tag is not None:
                self._chat.finish_ai_message()
                self._current_ai_tag = None

        elif event_type == EventType.TOOL_CALL:
            self._chat.add_tool_call(payload["tool_name"], payload["tool_input"])
            self._status.log_tool_call(payload["tool_name"], payload["tool_input"])

        elif event_type == EventType.TOOL_RESULT:
            self._chat.add_tool_result(payload["tool_name"], payload["result"])
            self._status.log_tool_result(payload["tool_name"], payload["result"])

        elif event_type == EventType.ERROR:
            self._chat.set_thinking(False)
            self._chat.add_error(payload["message"])
            self._status.log_error(payload["message"])

        elif event_type == EventType.DONE:
            if self._current_ai_tag is not None:
                self._chat.finish_ai_message()
                self._current_ai_tag = None
            self._chat.set_thinking(False)
            self._status.log_info("Turn complete.")

    # ------------------------------------------------------------------
    # Settings saved
    # ------------------------------------------------------------------

    def _on_settings_saved(self, updated: dict):
        self._status.log_system("Settings updated.")
        # Rebuild bridge with new simulation_mode preference
        sim = updated.get("fusion_simulation_mode", settings.simulation_mode)
        self._bridge._forced_sim = sim
        self._bridge.simulation_mode = sim
        if not sim:
            # Try to reconnect
            self.after(100, self._reconnect)
        else:
            self._status.set_fusion_status(connected=True, simulation=True)
            self._set_conn_label("◌  Simulation Mode", COLORS["warning"])
        # Rebuild Claude client (picks up new key/model)
        self._claude = ClaudeClient(settings, self._mcp)
        self._status.log_system(f"Model: {settings.model}")

    # ------------------------------------------------------------------
    # MCP hooks
    # ------------------------------------------------------------------

    def _confirm_tool(self, tool_name: str, inputs: dict) -> bool:
        import json
        return messagebox.askyesno(
            "Confirm Tool Execution",
            f"Claude wants to run:\n\n  {tool_name}\n\n"
            f"Inputs:\n{json.dumps(inputs, indent=2)}\n\nAllow?",
        )

    def _on_tool_result_hook(self, tool_name: str, inputs: dict, result: dict):
        pass  # handled via _on_claude_event

    # ------------------------------------------------------------------
    # Toolbar actions
    # ------------------------------------------------------------------

    def _reconnect(self):
        self._set_conn_label("◌  Connecting…", COLORS["warning"])
        self._status.log_system("Reconnecting to Fusion 360 add-in…")
        self._bridge.disconnect()
        self.after(200, self._init_connection)

    def _clear_chat(self):
        self._chat.clear_chat()
        self._claude.clear_history()
        self._status.log_system("Chat and conversation history cleared.")

    def _show_tools(self):
        win = tk.Toplevel(self)
        win.title("Available MCP Tools")
        win.configure(bg=COLORS["bg"])
        win.geometry("560x420")

        tk.Label(
            win,
            text="🔧  Registered MCP Tools",
            bg=COLORS["bg"],
            fg=COLORS["accent"],
            font=("SF Pro Display", 14, "bold"),
            pady=10,
        ).pack()

        from tkinter import scrolledtext
        txt = scrolledtext.ScrolledText(
            win,
            bg=COLORS["bg"],
            fg=COLORS["text"],
            font=("Courier New", 11),
            relief=tk.FLAT,
            padx=12,
            pady=8,
        )
        txt.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        txt.insert(tk.END, self._mcp.describe_tools())
        txt.configure(state=tk.DISABLED)

    def _install_addin(self):
        """Copy fusion_addin/ into Fusion 360's AddIns directory."""
        if not os.path.isdir(ADDIN_SRC):
            messagebox.showerror(
                "Add-in Not Found",
                f"Could not find fusion_addin/ at:\n{ADDIN_SRC}"
            )
            return

        try:
            if os.path.exists(ADDIN_DEST):
                shutil.rmtree(ADDIN_DEST)
            shutil.copytree(ADDIN_SRC, ADDIN_DEST)
            messagebox.showinfo(
                "Add-in Installed",
                f"✅ Fusion 360 MCP Add-in installed to:\n{ADDIN_DEST}\n\n"
                "Next steps:\n"
                "  1. In Fusion 360: Tools → Add-Ins → Scripts and Add-Ins\n"
                "  2. Click the Add-Ins tab\n"
                "  3. Find 'Fusion360MCP' and click ▶ Run\n"
                "  4. Click 'Reconnect' in this app"
            )
            self._status.log_success(f"Add-in installed to: {ADDIN_DEST}")
        except Exception as exc:
            messagebox.showerror("Install Failed", f"Could not install add-in:\n{exc}")
            self._status.log_error(f"Add-in install failed: {exc}")

    # ------------------------------------------------------------------
    # Window close
    # ------------------------------------------------------------------

    def _on_close(self):
        settings.set("window_width",  self.winfo_width())
        settings.set("window_height", self.winfo_height())
        settings.save()
        self._bridge.disconnect()
        self.destroy()
        sys.exit(0)
