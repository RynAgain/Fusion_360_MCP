"""
ui/chat_panel.py
Chat interface panel — displays conversation history and accepts user input.
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
import threading
from typing import Callable


# ---------------------------------------------------------------------------
# Colour palette (dark theme)
# ---------------------------------------------------------------------------
COLORS = {
    "bg":           "#1e1e2e",
    "surface":      "#2a2a3e",
    "surface2":     "#313145",
    "user_bubble":  "#3b4261",
    "ai_bubble":    "#1e3a5f",
    "tool_bubble":  "#2d3b2d",
    "accent":       "#7aa2f7",
    "accent2":      "#bb9af7",
    "success":      "#9ece6a",
    "warning":      "#e0af68",
    "error":        "#f7768e",
    "text":         "#c0caf5",
    "text_dim":     "#565f89",
    "border":       "#3b3b5c",
    "input_bg":     "#252535",
    "send_btn":     "#7aa2f7",
    "send_btn_fg":  "#1e1e2e",
}

FONT_MONO  = ("JetBrains Mono", 11) if True else ("Courier New", 11)
FONT_BODY  = ("SF Pro Text", 12)    if True else ("Helvetica", 12)
FONT_SMALL = ("SF Pro Text", 10)    if True else ("Helvetica", 10)


class ChatPanel(tk.Frame):
    """
    Left-side chat panel.
    Exposes:
        on_send_callback — set by parent to receive (user_text: str)
    """

    def __init__(self, parent, on_send: Callable[[str], None] | None = None, **kwargs):
        super().__init__(parent, bg=COLORS["bg"], **kwargs)
        self.on_send = on_send
        self._ai_buffer = ""          # accumulates streaming text
        self._ai_tag_id = 0           # unique tag per AI message
        self._thinking = False

        self._build_ui()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_ui(self):
        # ── Header ──────────────────────────────────────────────────────
        header = tk.Frame(self, bg=COLORS["surface"], pady=8)
        header.pack(fill=tk.X)
        tk.Label(
            header,
            text="💬  Fusion 360 AI Assistant",
            bg=COLORS["surface"],
            fg=COLORS["accent"],
            font=("SF Pro Display", 14, "bold"),
            padx=12,
        ).pack(side=tk.LEFT)

        self._status_label = tk.Label(
            header,
            text="● Ready",
            bg=COLORS["surface"],
            fg=COLORS["success"],
            font=FONT_SMALL,
            padx=12,
        )
        self._status_label.pack(side=tk.RIGHT)

        # ── Message area ────────────────────────────────────────────────
        msg_frame = tk.Frame(self, bg=COLORS["bg"])
        msg_frame.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

        self._chat_text = scrolledtext.ScrolledText(
            msg_frame,
            wrap=tk.WORD,
            state=tk.DISABLED,
            bg=COLORS["bg"],
            fg=COLORS["text"],
            font=FONT_BODY,
            relief=tk.FLAT,
            padx=16,
            pady=12,
            spacing1=4,
            spacing3=4,
            cursor="arrow",
            insertbackground=COLORS["accent"],
        )
        self._chat_text.pack(fill=tk.BOTH, expand=True)

        # Configure text tags
        self._chat_text.tag_configure("user_label",  foreground=COLORS["accent"],  font=("SF Pro Text", 10, "bold"))
        self._chat_text.tag_configure("user_text",   foreground=COLORS["text"],    font=FONT_BODY, lmargin1=16, lmargin2=16)
        self._chat_text.tag_configure("ai_label",    foreground=COLORS["accent2"], font=("SF Pro Text", 10, "bold"))
        self._chat_text.tag_configure("ai_text",     foreground=COLORS["text"],    font=FONT_BODY, lmargin1=16, lmargin2=16)
        self._chat_text.tag_configure("tool_label",  foreground=COLORS["success"], font=("SF Pro Text", 10, "bold"))
        self._chat_text.tag_configure("tool_text",   foreground=COLORS["text_dim"],font=FONT_SMALL, lmargin1=16, lmargin2=16)
        self._chat_text.tag_configure("error_label", foreground=COLORS["error"],   font=("SF Pro Text", 10, "bold"))
        self._chat_text.tag_configure("error_text",  foreground=COLORS["error"],   font=FONT_BODY, lmargin1=16, lmargin2=16)
        self._chat_text.tag_configure("separator",   foreground=COLORS["border"])
        self._chat_text.tag_configure("thinking",    foreground=COLORS["text_dim"], font=("SF Pro Text", 11, "italic"), lmargin1=16)

        # ── Divider ─────────────────────────────────────────────────────
        tk.Frame(self, bg=COLORS["border"], height=1).pack(fill=tk.X)

        # ── Input area ──────────────────────────────────────────────────
        input_frame = tk.Frame(self, bg=COLORS["surface"], pady=10, padx=12)
        input_frame.pack(fill=tk.X)

        # Prompt history
        self._history: list[str] = []
        self._history_idx = -1

        self._input_text = tk.Text(
            input_frame,
            height=3,
            bg=COLORS["input_bg"],
            fg=COLORS["text"],
            font=FONT_BODY,
            relief=tk.FLAT,
            padx=10,
            pady=8,
            wrap=tk.WORD,
            insertbackground=COLORS["accent"],
            highlightthickness=1,
            highlightcolor=COLORS["accent"],
            highlightbackground=COLORS["border"],
        )
        self._input_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._input_text.bind("<Return>",       self._on_return)
        self._input_text.bind("<Shift-Return>", lambda e: None)   # allow newline
        self._input_text.bind("<Up>",           self._history_up)
        self._input_text.bind("<Down>",         self._history_down)

        btn_frame = tk.Frame(input_frame, bg=COLORS["surface"])
        btn_frame.pack(side=tk.RIGHT, padx=(8, 0))

        self._send_btn = tk.Button(
            btn_frame,
            text="Send ↵",
            command=self._send,
            bg=COLORS["send_btn"],
            fg=COLORS["send_btn_fg"],
            font=("SF Pro Text", 11, "bold"),
            relief=tk.FLAT,
            padx=14,
            pady=8,
            cursor="hand2",
            activebackground=COLORS["accent2"],
            activeforeground=COLORS["send_btn_fg"],
        )
        self._send_btn.pack(pady=(0, 4))

        self._clear_btn = tk.Button(
            btn_frame,
            text="Clear",
            command=self.clear_chat,
            bg=COLORS["surface2"],
            fg=COLORS["text_dim"],
            font=FONT_SMALL,
            relief=tk.FLAT,
            padx=14,
            pady=4,
            cursor="hand2",
        )
        self._clear_btn.pack()

        # Hint label
        tk.Label(
            self,
            text="Enter to send  •  Shift+Enter for new line  •  ↑↓ for history",
            bg=COLORS["bg"],
            fg=COLORS["text_dim"],
            font=("SF Pro Text", 9),
        ).pack(pady=(2, 4))

    # ------------------------------------------------------------------
    # Input handling
    # ------------------------------------------------------------------

    def _on_return(self, event):
        self._send()
        return "break"   # prevent default newline insertion

    def _send(self):
        text = self._input_text.get("1.0", tk.END).strip()
        if not text or self._thinking:
            return
        self._input_text.delete("1.0", tk.END)
        self._history.append(text)
        self._history_idx = -1
        if self.on_send:
            self.on_send(text)

    def _history_up(self, event):
        if not self._history:
            return
        self._history_idx = min(self._history_idx + 1, len(self._history) - 1)
        self._set_input(self._history[-(self._history_idx + 1)])
        return "break"

    def _history_down(self, event):
        if self._history_idx <= 0:
            self._history_idx = -1
            self._set_input("")
            return "break"
        self._history_idx -= 1
        self._set_input(self._history[-(self._history_idx + 1)])
        return "break"

    def _set_input(self, text: str):
        self._input_text.delete("1.0", tk.END)
        self._input_text.insert("1.0", text)

    # ------------------------------------------------------------------
    # Message display helpers (thread-safe via after())
    # ------------------------------------------------------------------

    def _write(self, text: str, *tags):
        """Append text to the chat widget (must be called from main thread)."""
        self._chat_text.configure(state=tk.NORMAL)
        self._chat_text.insert(tk.END, text, tags)
        self._chat_text.configure(state=tk.DISABLED)
        self._chat_text.see(tk.END)

    def _schedule(self, fn, *args):
        """Schedule fn(*args) on the Tk main thread."""
        self.after(0, fn, *args)

    # ------------------------------------------------------------------
    # Public display methods (safe to call from any thread)
    # ------------------------------------------------------------------

    def add_user_message(self, text: str):
        def _do():
            self._write("\n👤  You\n", "user_label")
            self._write(text + "\n", "user_text")
            self._write("─" * 60 + "\n", "separator")
        self._schedule(_do)

    def start_ai_message(self):
        """Begin a new streaming AI message block."""
        self._ai_buffer = ""
        self._ai_tag_id += 1
        tag = f"ai_stream_{self._ai_tag_id}"

        def _do():
            self._write("\n🤖  Claude\n", "ai_label")
            # Insert a placeholder with a unique tag we'll append to
            self._chat_text.configure(state=tk.NORMAL)
            self._chat_text.insert(tk.END, "", (tag, "ai_text"))
            self._chat_text.configure(state=tk.DISABLED)
            self._chat_text.see(tk.END)

        self._schedule(_do)
        return tag

    def append_ai_text(self, text: str, tag: str):
        """Append a text delta to the current AI message."""
        self._ai_buffer += text

        def _do():
            self._chat_text.configure(state=tk.NORMAL)
            self._chat_text.insert(tk.END, text, (tag, "ai_text"))
            self._chat_text.configure(state=tk.DISABLED)
            self._chat_text.see(tk.END)

        self._schedule(_do)

    def finish_ai_message(self):
        def _do():
            self._write("\n" + "─" * 60 + "\n", "separator")
        self._schedule(_do)

    def add_tool_call(self, tool_name: str, tool_input: dict):
        def _do():
            self._write(f"\n🔧  Tool Call: {tool_name}\n", "tool_label")
            import json
            self._write(json.dumps(tool_input, indent=2) + "\n", "tool_text")
        self._schedule(_do)

    def add_tool_result(self, tool_name: str, result: dict):
        def _do():
            status = result.get("status", "?")
            icon = "✅" if status == "success" else "⚠️" if status == "simulation" else "❌"
            self._write(f"{icon}  Result ({tool_name}): {result.get('message', str(result))}\n", "tool_text")
        self._schedule(_do)

    def add_error(self, message: str):
        def _do():
            self._write(f"\n❌  Error\n", "error_label")
            self._write(message + "\n", "error_text")
            self._write("─" * 60 + "\n", "separator")
        self._schedule(_do)

    def set_thinking(self, thinking: bool):
        """Show/hide the 'Claude is thinking…' indicator."""
        self._thinking = thinking

        def _do():
            if thinking:
                self._chat_text.configure(state=tk.NORMAL)
                self._chat_text.insert(tk.END, "\n⏳  Claude is thinking…\n", "thinking")
                self._chat_text.configure(state=tk.DISABLED)
                self._chat_text.see(tk.END)
                self._send_btn.configure(state=tk.DISABLED, text="…")
                self._status_label.configure(text="● Thinking", fg=COLORS["warning"])
            else:
                # Remove the thinking line
                self._chat_text.configure(state=tk.NORMAL)
                content = self._chat_text.get("1.0", tk.END)
                thinking_line = "\n⏳  Claude is thinking…\n"
                if thinking_line in content:
                    idx = content.rfind(thinking_line)
                    start = f"1.0 + {idx} chars"
                    end   = f"1.0 + {idx + len(thinking_line)} chars"
                    self._chat_text.delete(start, end)
                self._chat_text.configure(state=tk.DISABLED)
                self._send_btn.configure(state=tk.NORMAL, text="Send ↵")
                self._status_label.configure(text="● Ready", fg=COLORS["success"])

        self._schedule(_do)

    def clear_chat(self):
        def _do():
            self._chat_text.configure(state=tk.NORMAL)
            self._chat_text.delete("1.0", tk.END)
            self._chat_text.configure(state=tk.DISABLED)
        self._schedule(_do)

    def set_on_send(self, callback: Callable[[str], None]):
        self.on_send = callback
