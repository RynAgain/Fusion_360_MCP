"""
ui/settings_panel.py
Settings panel — API key, model selection, system prompt, security options.
"""

import tkinter as tk
from tkinter import ttk, messagebox
from typing import Callable

COLORS = {
    "bg":        "#1e1e2e",
    "surface":   "#2a2a3e",
    "surface2":  "#313145",
    "accent":    "#7aa2f7",
    "accent2":   "#bb9af7",
    "success":   "#9ece6a",
    "warning":   "#e0af68",
    "error":     "#f7768e",
    "text":      "#c0caf5",
    "text_dim":  "#565f89",
    "border":    "#3b3b5c",
    "input_bg":  "#252535",
}

FONT_BODY  = ("SF Pro Text", 12)
FONT_SMALL = ("SF Pro Text", 10)
FONT_LABEL = ("SF Pro Text", 11, "bold")

AVAILABLE_MODELS = [
    "claude-opus-4-5",
    "claude-sonnet-4-5",
    "claude-haiku-4-5",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-haiku-20241022",
    "claude-3-opus-20240229",
]


class SettingsPanel(tk.Frame):
    """
    Right-side settings panel.
    Calls on_save(updated_settings_dict) when the user clicks Save.
    """

    def __init__(self, parent, settings, on_save: Callable[[dict], None] | None = None, **kwargs):
        super().__init__(parent, bg=COLORS["bg"], **kwargs)
        self.settings = settings
        self.on_save = on_save
        self._build_ui()
        self._load_values()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_ui(self):
        # ── Header ──────────────────────────────────────────────────────
        header = tk.Frame(self, bg=COLORS["surface"], pady=8)
        header.pack(fill=tk.X)
        tk.Label(
            header,
            text="⚙️  Settings",
            bg=COLORS["surface"],
            fg=COLORS["accent"],
            font=("SF Pro Display", 14, "bold"),
            padx=12,
        ).pack(side=tk.LEFT)

        # ── Scrollable content ──────────────────────────────────────────
        canvas = tk.Canvas(self, bg=COLORS["bg"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        self._scroll_frame = tk.Frame(canvas, bg=COLORS["bg"])

        self._scroll_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=self._scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Bind mousewheel
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))

        f = self._scroll_frame  # shorthand

        # ── Section: API ────────────────────────────────────────────────
        self._section(f, "🔑  Anthropic API")

        self._api_key_var = tk.StringVar()
        self._labeled_entry(f, "API Key", self._api_key_var, show="•", width=36)

        tk.Label(
            f,
            text="Get your key at console.anthropic.com",
            bg=COLORS["bg"],
            fg=COLORS["text_dim"],
            font=FONT_SMALL,
        ).pack(anchor=tk.W, padx=16, pady=(0, 8))

        # Model selector
        self._section(f, "🧠  Model")
        self._model_var = tk.StringVar()
        model_frame = tk.Frame(f, bg=COLORS["bg"])
        model_frame.pack(fill=tk.X, padx=16, pady=4)
        tk.Label(model_frame, text="Model", bg=COLORS["bg"], fg=COLORS["text"], font=FONT_LABEL, width=18, anchor=tk.W).pack(side=tk.LEFT)
        model_menu = ttk.Combobox(
            model_frame,
            textvariable=self._model_var,
            values=AVAILABLE_MODELS,
            state="readonly",
            width=30,
        )
        model_menu.pack(side=tk.LEFT)

        self._max_tokens_var = tk.StringVar()
        self._labeled_entry(f, "Max Tokens", self._max_tokens_var, width=10)

        # ── Section: System Prompt ───────────────────────────────────────
        self._section(f, "📝  System Prompt")
        tk.Label(f, text="Customize Claude's behaviour:", bg=COLORS["bg"], fg=COLORS["text"], font=FONT_SMALL).pack(anchor=tk.W, padx=16)
        self._system_prompt_text = tk.Text(
            f,
            height=6,
            bg=COLORS["input_bg"],
            fg=COLORS["text"],
            font=("SF Pro Text", 11),
            relief=tk.FLAT,
            padx=8,
            pady=6,
            wrap=tk.WORD,
            insertbackground=COLORS["accent"],
            highlightthickness=1,
            highlightcolor=COLORS["accent"],
            highlightbackground=COLORS["border"],
        )
        self._system_prompt_text.pack(fill=tk.X, padx=16, pady=(4, 8))

        reset_btn = tk.Button(
            f,
            text="Reset to Default",
            command=self._reset_system_prompt,
            bg=COLORS["surface2"],
            fg=COLORS["text_dim"],
            font=FONT_SMALL,
            relief=tk.FLAT,
            padx=10,
            pady=4,
            cursor="hand2",
        )
        reset_btn.pack(anchor=tk.W, padx=16, pady=(0, 8))

        # ── Section: Fusion 360 ──────────────────────────────────────────
        self._section(f, "🔩  Fusion 360")

        self._sim_mode_var = tk.BooleanVar()
        self._checkbox(f, "Simulation Mode (no real Fusion 360 needed)", self._sim_mode_var)

        # ── Section: Security ────────────────────────────────────────────
        self._section(f, "🔒  Security")

        self._require_confirm_var = tk.BooleanVar()
        self._checkbox(f, "Require confirmation before executing tools", self._require_confirm_var)

        self._max_rpm_var = tk.StringVar()
        self._labeled_entry(f, "Max Requests / Minute", self._max_rpm_var, width=8)

        # ── Save / Cancel buttons ────────────────────────────────────────
        btn_frame = tk.Frame(f, bg=COLORS["bg"], pady=16)
        btn_frame.pack(fill=tk.X, padx=16)

        tk.Button(
            btn_frame,
            text="💾  Save Settings",
            command=self._save,
            bg=COLORS["accent"],
            fg=COLORS["bg"],
            font=("SF Pro Text", 12, "bold"),
            relief=tk.FLAT,
            padx=16,
            pady=8,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(0, 8))

        tk.Button(
            btn_frame,
            text="↩  Revert",
            command=self._load_values,
            bg=COLORS["surface2"],
            fg=COLORS["text_dim"],
            font=FONT_BODY,
            relief=tk.FLAT,
            padx=16,
            pady=8,
            cursor="hand2",
        ).pack(side=tk.LEFT)

        # ── Version footer ───────────────────────────────────────────────
        tk.Label(
            f,
            text="Fusion 360 MCP Controller  v0.2.0",
            bg=COLORS["bg"],
            fg=COLORS["text_dim"],
            font=("SF Pro Text", 9),
        ).pack(pady=(8, 16))

    # ------------------------------------------------------------------
    # Widget helpers
    # ------------------------------------------------------------------

    def _section(self, parent, title: str):
        tk.Frame(parent, bg=COLORS["border"], height=1).pack(fill=tk.X, padx=8, pady=(12, 0))
        tk.Label(
            parent,
            text=title,
            bg=COLORS["bg"],
            fg=COLORS["accent2"],
            font=("SF Pro Text", 12, "bold"),
            padx=16,
        ).pack(anchor=tk.W, pady=(4, 4))

    def _labeled_entry(self, parent, label: str, var: tk.StringVar, show: str = "", width: int = 24):
        row = tk.Frame(parent, bg=COLORS["bg"])
        row.pack(fill=tk.X, padx=16, pady=4)
        tk.Label(row, text=label, bg=COLORS["bg"], fg=COLORS["text"], font=FONT_LABEL, width=22, anchor=tk.W).pack(side=tk.LEFT)
        entry = tk.Entry(
            row,
            textvariable=var,
            show=show,
            width=width,
            bg=COLORS["input_bg"],
            fg=COLORS["text"],
            font=FONT_BODY,
            relief=tk.FLAT,
            insertbackground=COLORS["accent"],
            highlightthickness=1,
            highlightcolor=COLORS["accent"],
            highlightbackground=COLORS["border"],
        )
        entry.pack(side=tk.LEFT, ipady=4, padx=(0, 4))

        # Toggle show/hide for password fields
        if show:
            def toggle_show():
                current = entry.cget("show")
                entry.configure(show="" if current else "•")
            tk.Button(
                row,
                text="👁",
                command=toggle_show,
                bg=COLORS["surface2"],
                fg=COLORS["text_dim"],
                font=FONT_SMALL,
                relief=tk.FLAT,
                padx=4,
                cursor="hand2",
            ).pack(side=tk.LEFT)

    def _checkbox(self, parent, label: str, var: tk.BooleanVar):
        row = tk.Frame(parent, bg=COLORS["bg"])
        row.pack(fill=tk.X, padx=16, pady=4)
        cb = tk.Checkbutton(
            row,
            text=label,
            variable=var,
            bg=COLORS["bg"],
            fg=COLORS["text"],
            selectcolor=COLORS["surface2"],
            activebackground=COLORS["bg"],
            activeforeground=COLORS["accent"],
            font=FONT_BODY,
            cursor="hand2",
        )
        cb.pack(side=tk.LEFT)

    # ------------------------------------------------------------------
    # Load / Save
    # ------------------------------------------------------------------

    def _load_values(self):
        """Populate widgets from current settings."""
        self._api_key_var.set(self.settings.get("anthropic_api_key", ""))
        self._model_var.set(self.settings.get("model", "claude-opus-4-5"))
        self._max_tokens_var.set(str(self.settings.get("max_tokens", 4096)))
        self._sim_mode_var.set(self.settings.get("fusion_simulation_mode", True))
        self._require_confirm_var.set(self.settings.get("require_confirmation", False))
        self._max_rpm_var.set(str(self.settings.get("max_requests_per_minute", 10)))

        self._system_prompt_text.delete("1.0", tk.END)
        self._system_prompt_text.insert("1.0", self.settings.get("system_prompt", ""))

    def _reset_system_prompt(self):
        from config.settings import DEFAULTS
        self._system_prompt_text.delete("1.0", tk.END)
        self._system_prompt_text.insert("1.0", DEFAULTS["system_prompt"])

    def _save(self):
        """Validate and persist settings."""
        try:
            max_tokens = int(self._max_tokens_var.get())
            if max_tokens < 1 or max_tokens > 32000:
                raise ValueError("Max tokens must be between 1 and 32000.")
            max_rpm = int(self._max_rpm_var.get())
            if max_rpm < 1:
                raise ValueError("Max requests/minute must be ≥ 1.")
        except ValueError as exc:
            messagebox.showerror("Invalid Settings", str(exc))
            return

        updated = {
            "anthropic_api_key":     self._api_key_var.get().strip(),
            "model":                 self._model_var.get(),
            "max_tokens":            max_tokens,
            "system_prompt":         self._system_prompt_text.get("1.0", tk.END).strip(),
            "fusion_simulation_mode": self._sim_mode_var.get(),
            "require_confirmation":  self._require_confirm_var.get(),
            "max_requests_per_minute": max_rpm,
        }

        self.settings.update(updated)

        if self.on_save:
            self.on_save(updated)

        messagebox.showinfo("Settings Saved", "Settings have been saved successfully.")
