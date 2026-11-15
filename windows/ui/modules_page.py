"""
PulseBox Plugins Page
======================
Lists all loaded plugins as collapsible cards.
Each card shows the module name with a ＋/－ toggle button.
Expanding the card reveals description, version, author and
the module's own UI panel (settings, controls, etc.).
"""

import tkinter as tk
from .theme import (ACCENT, ACCENT2, BG_CARD, BG_DARK, BG_PANEL,
                    BORDER, DANGER, FONT_BODY, FONT_DISPLAY, FONT_SMALL,
                    FONT_SUBHEAD, FONT_TINY, NEON_BLUE, PAD, PAD_SM,
                    SUCCESS, TEXT_DIM, TEXT_PRIMARY, TEXT_SECONDARY, WARNING)
from .widgets import ScrollableFrame


# One accent colour per module slot (cycles if more modules than colours)
_MODULE_COLOURS = [
    "#e8b004",  # amber
    "#ff2d78",  # pink
    "#00d4ff",  # cyan
    "#00cc88",  # green
    "#cc44ff",  # purple
    "#ff6b35",  # orange
    "#4488ff",  # blue
    "#ff4444",  # red
]


class _ModuleCard(tk.Frame):
    """
    A collapsible card for a single module.

    Collapsed:  ╔══════════════════════════════════════╗
                ║  ⊞  Module Name          v1.0  [+]  ║
                ╚══════════════════════════════════════╝

    Expanded:   ╔══════════════════════════════════════╗
                ║  ⊟  Module Name          v1.0  [−]  ║
                ║──────────────────────────────────────║
                ║  Description text                    ║
                ║  by Author                           ║
                ║──────────────────────────────────────║
                ║  [module's own UI panel]             ║
                ╚══════════════════════════════════════╝
    """

    def __init__(self, parent, module, accent: str,
                 start_expanded: bool = False, scroll_ref=None, **kwargs):
        super().__init__(parent, bg=BG_PANEL,
                         highlightthickness=1,
                         highlightbackground=BORDER,
                         **kwargs)
        self._module     = module
        self._accent     = accent
        self._expanded   = False
        self._panel_built = False
        self._scroll_ref  = scroll_ref

        self._build_header()
        self._build_body()

        if start_expanded:
            self._set_expanded(True, animate=False)

    # ------------------------------------------------------------------
    # Header (always visible)
    # ------------------------------------------------------------------

    def _build_header(self):
        self._header = tk.Frame(self, bg=BG_PANEL, cursor="hand2")
        self._header.pack(fill="x")

        # Left accent stripe
        tk.Frame(self._header, bg=self._accent,
                 width=4).pack(side="left", fill="y")

        # Toggle icon (⊞ / ⊟)
        self._toggle_lbl = tk.Label(
            self._header,
            text="⊞",
            font=("Helvetica", 16, "bold"),
            fg=self._accent, bg=BG_PANEL,
            padx=8, pady=10)
        self._toggle_lbl.pack(side="left")

        # Module name
        tk.Label(self._header,
                 text=module_name(self._module),
                 font=FONT_SUBHEAD,
                 fg=TEXT_PRIMARY, bg=BG_PANEL,
                 pady=10).pack(side="left")

        # Version (dim, right-padded)
        tk.Label(self._header,
                 text=f"v{self._module.MODULE_VERSION}",
                 font=FONT_TINY, fg=TEXT_PRIMARY,
                 bg=BG_PANEL, padx=6).pack(side="left")

        # Status dot
        tk.Label(self._header,
                 text="● loaded",
                 font=FONT_TINY, fg=SUCCESS,
                 bg=BG_PANEL).pack(side="left")

        # Expand / collapse button (right side)
        self._btn = tk.Button(
            self._header,
            text=" + ",
            font=("Helvetica", 14, "bold"),
            fg=self._accent, bg="#000000",
            relief="flat", bd=0, cursor="hand2",
            padx=14, pady=8,
            activeforeground=TEXT_PRIMARY,
            activebackground=BG_CARD,
            command=self._on_toggle)
        self._btn.pack(side="right")

        # Whole header row is clickable too
        for w in [self._header, self._toggle_lbl]:
            w.bind("<Button-1>", lambda _e: self._on_toggle())

    # ------------------------------------------------------------------
    # Body (hidden when collapsed)
    # ------------------------------------------------------------------

    def _build_body(self):
        self._body = tk.Frame(self, bg=BG_CARD)
        # Not packed yet — shown on expand

        # Separator line matching accent colour
        tk.Frame(self._body, bg=self._accent, height=2).pack(fill="x")

        inner = tk.Frame(self._body, bg=BG_CARD, padx=PAD, pady=PAD_SM)
        inner.pack(fill="both", expand=True)

        # Description
        tk.Label(inner,
                 text=self._module.MODULE_DESCRIPTION,
                 font=FONT_SMALL, fg=TEXT_PRIMARY, bg=BG_CARD,
                 wraplength=700, justify="left",
                 anchor="w").pack(fill="x", pady=(0, 4))

        # Author
        tk.Label(inner,
                 text=f"by {self._module.MODULE_AUTHOR}",
                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_CARD,
                 anchor="w").pack(fill="x", pady=(0, PAD_SM))

        # Divider before the module's own panel
        tk.Frame(inner, bg=BORDER, height=1).pack(fill="x", pady=(4, PAD_SM))

        # Module's custom UI panel (built lazily on first expand)
        self._panel_container = tk.Frame(inner, bg=BG_CARD)
        self._panel_container.pack(fill="both", expand=True)

    def _build_panel_lazy(self):
        if self._panel_built:
            return
        self._panel_built = True
        try:
            panel = self._module.get_ui_panel(self._panel_container)
            if panel:
                panel.pack(fill="both", expand=True)
            else:
                tk.Label(self._panel_container,
                         text="(no settings panel for this module)",
                         font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_CARD).pack(anchor="w")
        except Exception as e:
            tk.Label(self._panel_container,
                     text=f"Panel error: {e}",
                     font=FONT_TINY, fg=DANGER, bg=BG_CARD).pack(anchor="w")

    # ------------------------------------------------------------------
    # Toggle
    # ------------------------------------------------------------------

    def _on_toggle(self):
        self._set_expanded(not self._expanded)

    def _set_expanded(self, expanded: bool, animate: bool = True):
        self._expanded = expanded
        if expanded:
            self._build_panel_lazy()
            self._body.pack(fill="x", after=self._header)
            self._btn.config(text=" − ")
            self._toggle_lbl.config(text="⊟")
            self.config(highlightbackground=self._accent)
            self.after(60, self._rebind_and_scroll)
        else:
            self._body.pack_forget()
            self._btn.config(text=" + ")
            self._toggle_lbl.config(text="⊞")
            self.config(highlightbackground=BORDER)

    def _rebind_and_scroll(self):
        if self._scroll_ref:
            self._scroll_ref.bind_all_children_scroll(self._body)
            self._scroll_ref.refresh_scrollregion()
            self._scroll_ref.scroll_to_widget(self)
        else:
            from .widgets import ScrollableFrame
            w = self.master
            while w is not None:
                if isinstance(w, ScrollableFrame):
                    w.bind_all_children_scroll(self._body)
                    w.refresh_scrollregion()
                    w.scroll_to_widget(self)
                    return
                w = getattr(w, "master", None)

    def _scroll_into_view(self):
        """Walk up to find the ScrollableFrame and scroll this card into view."""
        from .widgets import ScrollableFrame
        w = self.master
        while w is not None:
            if isinstance(w, ScrollableFrame):
                w.scroll_to_widget(self)
                return
            w = getattr(w, "master", None)


def module_name(module) -> str:
    return getattr(module, "MODULE_NAME", type(module).__name__)


# ---------------------------------------------------------------------------
# ModulesPage
# ---------------------------------------------------------------------------

class ModulesPage(tk.Frame):
    def __init__(self, parent, module_registry, **kwargs):
        super().__init__(parent, bg=BG_DARK, **kwargs)
        self._registry = module_registry
        self._cards: list[_ModuleCard] = []

    def refresh(self):
        for w in self.winfo_children():
            w.destroy()
        self._cards = []
        self._build()

    def _build(self):
        # ── Header ────────────────────────────────────────────────────
        hdr = tk.Frame(self, bg=BG_DARK)
        hdr.pack(fill="x", padx=PAD, pady=(PAD, 0))

        tk.Label(hdr, text="PLUGINS", font=FONT_DISPLAY,
                 fg=ACCENT, bg=BG_DARK).pack(side="left")

        modules = sorted(self._registry.get_all(), key=lambda m: m.MODULE_NAME.upper())
        tk.Label(hdr,
                 text=f"{len(modules)} loaded",
                 font=FONT_SMALL, fg=TEXT_PRIMARY, bg=BG_DARK).pack(
            side="left", padx=12)

        # Expand all / Collapse all buttons
        if modules:
            tk.Button(hdr, text="Expand all",
                      font=FONT_TINY, fg=ACCENT, bg=BG_CARD,
                      relief="flat", bd=0, cursor="hand2",
                      padx=10, pady=4,
                      command=self._expand_all).pack(side="right", padx=4)
            tk.Button(hdr, text="Collapse all",
                      font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_CARD,
                      relief="flat", bd=0, cursor="hand2",
                      padx=10, pady=4,
                      command=self._collapse_all).pack(side="right", padx=4)

        tk.Label(self,
                 text="Click a module name or the + button to expand its settings.",
                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_DARK).pack(
            anchor="w", padx=PAD, pady=(2, PAD_SM))

        # ── Module list ───────────────────────────────────────────────
        scroll = ScrollableFrame(self, bg=BG_DARK)
        scroll.pack(fill="both", expand=True)
        self._scroll_ref = scroll  # kept for rebind on expand

        if not modules:
            self._show_empty(scroll.inner)
            return

        for i, m in enumerate(modules):
            accent = _MODULE_COLOURS[i % len(_MODULE_COLOURS)]
            card = _ModuleCard(scroll.inner, m, accent=accent,
                               scroll_ref=scroll)
            card.pack(fill="x", padx=PAD, pady=(0, PAD_SM))
            self._cards.append(card)

        scroll.bind_all_children_scroll(scroll.inner)
        scroll.refresh_scrollregion()

    def _expand_all(self):
        for card in self._cards:
            card._set_expanded(True)

    def _collapse_all(self):
        for card in self._cards:
            card._set_expanded(False)

    def _show_empty(self, parent):
        tk.Label(parent, text="No plugins loaded.",
                 font=FONT_BODY, fg=TEXT_PRIMARY, bg=BG_DARK).pack(
            padx=PAD, pady=PAD)

        example = (
            "# Example module skeleton:\n\n"
            "from core.config import JukeboxModule\n\n"
            "class MyModule(JukeboxModule):\n"
            "    MODULE_ID      = 'my_module'\n"
            "    MODULE_NAME    = 'My Module'\n"
            "    MODULE_VERSION = '1.0.0'\n\n"
            "    def on_load(self, app):\n"
            "        super().on_load(app)\n\n"
            "    def get_ui_panel(self, parent):\n"
            "        import tkinter as tk\n"
            "        f = tk.Frame(parent)\n"
            "        tk.Label(f, text='Hello!').pack()\n"
            "        return f\n"
        )
        code_frame = tk.Frame(parent, bg="#0d0d18", padx=PAD, pady=PAD)
        code_frame.pack(fill="x", padx=PAD, pady=PAD)
        tk.Label(code_frame, text=example, font=("Courier", 10),
                 fg=NEON_BLUE, bg="#0d0d18",
                 justify="left", anchor="w").pack(fill="x")
