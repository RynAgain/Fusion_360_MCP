"""
ui/status_panel.py
Status / activity log panel — shows live tool calls, results, and system events.
"""

import tkinter as tk
from tkinter import scrolledtext
import datetime
from typing import Any

COLORS = {
    "bg":       "#1e1e2e",
    "surface":  "#2a2a3e",
    "surface2": "#313145",
    "accent":   "#7aa2f7",
    "accent2":  "#bb9af7",
    "success":  "#9ece6a",
    "warning":  "#e0af68",
    "error":    "#f7768e",
    "info":     "#7dcfff",
    "text":     "#c0caf5",
    "text_dim": "#565f89",
    "border":   "#3b3b5c",
}

FONT_MONO  = ("JetBrains Mono", 10)
FONT_SMALL = ("SF Pro Text", 10)


class StatusPanel(tk.Frame):
    """
    Bottom status / log panel.
    Displays timestamped log entries with colour-coded severity levels.
    """

    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg=COLORS["bg"], **kwargs)
        self._entry_count = 0
        self._build_ui()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_ui(self):
        # ── Header bar ──────────────────────────────────────────────────
        header = tk.Frame(self, bg=COLORS["surface"], pady=5)
        header.pack(fill=tk.X)

        tk.Label(
            header,
            text="📋  Activity Log",
            bg=COLORS["surface"],
            fg=COLORS["accent"],
            font=("SF Pro Display", 12, "bold"),
            padx=12,
        ).pack(side=tk.LEFT)

        # Fusion connection indicator
        self._fusion_label = tk.Label(
            header,
            text="🔩 Fusion: Simulation",
            bg=COLORS["surface"],
            fg=COLORS["warning"],
            font=FONT_SMALL,
            padx=8,
        )
        self._fusion_label.pack(side=tk.LEFT, padx=8)

        # Clear button
        tk.Button(
            header,
            text="Clear Log",
            command=self._clear,
            bg=COLORS["surface2"],
            fg=COLORS["text_dim"],
            font=FONT_SMALL,
            relief=tk.FLAT,
            padx=8,
            pady=2,
            cursor="hand2",
        ).pack(side=tk.RIGHT, padx=8)

        # Entry counter
        self._count_label = tk.Label(
            header,
            text="0 entries",
            bg=COLORS["surface"],
            fg=COLORS["text_dim"],
            font=FONT_SMALL,
            padx=8,
        )
        self._count_label.pack(side=tk.RIGHT)

        # ── Log text area ────────────────────────────────────────────────
        self._log = scrolledtext.ScrolledText(
            self,
            wrap=tk.WORD,
            state=tk.DISABLED,
            bg=COLORS["bg"],
            fg=COLORS["text"],
            font=FONT_MONO,
            relief=tk.FLAT,
            padx=10,
            pady=6,
            height=8,
            cursor="arrow",
        )
        self._log.pack(fill=tk.BOTH, expand=True)

        # Configure tags
        self._log.tag_configure("ts",      foreground=COLORS["text_dim"])
        self._log.tag_configure("info",    foreground=COLORS["info"])
        self._log.tag_configure("success", foreground=COLORS["success"])
        self._log.tag_configure("warning", foreground=COLORS["warning"])
        self._log.tag_configure("error",   foreground=COLORS["error"])
        self._log.tag_configure("tool",    foreground=COLORS["accent2"])
        self._log.tag_configure("result",  foreground=COLORS["text_dim"])
        self._log.tag_configure("system",  foreground=COLORS["accent"])

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ts(self) -> str:
        return datetime.datetime.now().strftime("%H:%M:%S")

    def _append(self, level_tag: str, icon: str, message: str):
        """Append a log line (must be called from main thread or via after())."""
        self._entry_count += 1
        self._log.configure(state=tk.NORMAL)
        self._log.insert(tk.END, f"[{self._ts()}] ", "ts")
        self._log.insert(tk.END, f"{icon} {message}\n", level_tag)
        self._log.configure(state=tk.DISABLED)
        self._log.see(tk.END)
        self._count_label.configure(text=f"{self._entry_count} entries")

    def _schedule(self, fn, *args):
        self.after(0, fn, *args)

    def _clear(self):
        self._log.configure(state=tk.NORMAL)
        self._log.delete("1.0", tk.END)
        self._log.configure(state=tk.DISABLED)
        self._entry_count = 0
        self._count_label.configure(text="0 entries")

    # ------------------------------------------------------------------
    # Public logging API (thread-safe)
    # ------------------------------------------------------------------

    def log_info(self, message: str):
        self._schedule(self._append, "info", "ℹ", message)

    def log_success(self, message: str):
        self._schedule(self._append, "success", "✅", message)

    def log_warning(self, message: str):
        self._schedule(self._append, "warning", "⚠️", message)

    def log_error(self, message: str):
        self._schedule(self._append, "error", "❌", message)

    def log_tool_call(self, tool_name: str, inputs: dict[str, Any]):
        import json
        short = json.dumps(inputs)
        if len(short) > 80:
            short = short[:77] + "…"
        self._schedule(self._append, "tool", "🔧", f"TOOL → {tool_name}({short})")

    def log_tool_result(self, tool_name: str, result: dict[str, Any]):
        status = result.get("status", "?")
        msg    = result.get("message", str(result))
        if len(msg) > 100:
            msg = msg[:97] + "…"
        tag = "success" if status == "success" else "warning" if status == "simulation" else "error"
        self._schedule(self._append, tag, "↩", f"RESULT ← {tool_name}: [{status}] {msg}")

    def log_system(self, message: str):
        self._schedule(self._append, "system", "⚙", message)

    # ------------------------------------------------------------------
    # Fusion connection status
    # ------------------------------------------------------------------

    def set_fusion_status(self, connected: bool, simulation: bool = False):
        def _do():
            if simulation:
                self._fusion_label.configure(text="🔩 Fusion: Simulation", fg=COLORS["warning"])
            elif connected:
                self._fusion_label.configure(text="🔩 Fusion: Connected", fg=COLORS["success"])
            else:
                self._fusion_label.configure(text="🔩 Fusion: Disconnected", fg=COLORS["error"])
        self._schedule(_do)
