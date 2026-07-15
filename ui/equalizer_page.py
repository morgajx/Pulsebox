"""
PulseBox Graphic Equaliser Page
10-band EQ using VLC's libvlc_audio_equalizer API.
Sits between Modules and Settings tabs.
"""

import json
import os
import tkinter as tk
from tkinter import ttk

from .theme import *

EQ_SAVE_PATH = os.path.expanduser("~/.pulsebox_eq.json")

BAND_LABELS = ["31Hz", "63Hz", "125Hz", "250Hz", "500Hz",
               "1kHz", "2kHz", "4kHz", "8kHz", "16kHz"]


class EqualizerPage(tk.Frame):

    def __init__(self, parent, player, **kwargs):
        super().__init__(parent, bg=BG_DARK, **kwargs)
        self._player  = player
        self._enabled = tk.BooleanVar(value=False)
        self._preamp  = tk.DoubleVar(value=0.0)
        self._bands   = [tk.DoubleVar(value=0.0) for _ in range(10)]
        self._preset_var = tk.StringVar(value="Flat")
        self._sliders = []
        self._load_saved()
        self._build()

    # ------------------------------------------------------------------
    # Build UI
    # ------------------------------------------------------------------

    def _build(self):
        # ── Header ────────────────────────────────────────────────────
        hdr = tk.Frame(self, bg=BG_DARK)
        hdr.pack(fill="x", padx=PAD, pady=(PAD, 4))

        tk.Label(hdr, text="EQUALISER", font=FONT_DISPLAY,
                 fg=ACCENT, bg=BG_DARK).pack(side="left")

        # Enable toggle
        tk.Checkbutton(hdr, text="Enable",
                       variable=self._enabled,
                       font=FONT_BODY, fg=TEXT_PRIMARY,
                       bg=BG_DARK, activebackground=BG_DARK,
                       selectcolor=BG_CARD, cursor="hand2",
                       command=self._on_toggle).pack(side="left", padx=16)

        # Preset dropdown
        tk.Label(hdr, text="Preset:", font=FONT_SMALL,
                 fg=TEXT_PRIMARY, bg=BG_DARK).pack(side="left")

        presets = list(self._player.EQ_PRESETS.keys()) if hasattr(
            self._player, 'EQ_PRESETS') else ["Flat"]
        combo = ttk.Combobox(hdr, textvariable=self._preset_var,
                             values=presets, state="readonly",
                             font=FONT_SMALL, width=14)
        combo.pack(side="left", padx=(4, 8), ipady=2)
        combo.bind("<<ComboboxSelected>>", self._on_preset)

        tk.Button(hdr, text="Save", font=FONT_SMALL,
                  fg=BG_DARK, bg=ACCENT,
                  relief="flat", bd=0, cursor="hand2",
                  padx=10, pady=4,
                  command=self._save).pack(side="right")

        tk.Button(hdr, text="Reset", font=FONT_SMALL,
                  fg=TEXT_PRIMARY, bg=BG_CARD,
                  relief="flat", bd=0, cursor="hand2",
                  padx=10, pady=4,
                  command=self._reset).pack(side="right", padx=4)

        # ── Preamp + band sliders — centred vertically ───────────────
        eq_outer = tk.Frame(self, bg=BG_PANEL)
        eq_outer.pack(fill="both", expand=True, padx=PAD, pady=(0, PAD))

        # Bottom-justified container — sliders anchor to bottom of panel
        eq_frame = tk.Frame(eq_outer, bg=BG_PANEL)
        eq_frame.place(relx=0.5, rely=1.0, anchor="s")

        # Wider vertical slider style (1.5× default)
        style = ttk.Style()
        style.configure("EQ.Vertical.TScale",
                        sliderlength=24,
                        sliderwidth=18,
                        troughcolor=BG_DARK,
                        background=BG_PANEL)

        # Preamp column
        preamp_col = tk.Frame(eq_frame, bg=BG_PANEL)
        preamp_col.pack(side="left", padx=(PAD, PAD * 2), anchor="s")

        tk.Label(preamp_col, text="PRE\nAMP",
                 font=FONT_TINY, fg=TEXT_PRIMARY,
                 bg=BG_PANEL, justify="center").pack()

        preamp_lbl = tk.Label(preamp_col,
                              text=f"{self._preamp.get():+.0f}",
                              font=FONT_SMALL, fg=ACCENT, bg=BG_PANEL)
        preamp_lbl.pack()

        preamp_sl = ttk.Scale(preamp_col, from_=20, to=-20,
                              variable=self._preamp,
                              orient="vertical", length=300,
                              style="EQ.Vertical.TScale",
                              command=lambda v, l=preamp_lbl:
                              (l.config(text=f"{float(v):+.0f}"),
                               self._on_change()))
        preamp_sl.pack()

        tk.Label(preamp_col, text="0dB", font=FONT_TINY,
                 fg=TEXT_PRIMARY, bg=BG_PANEL).pack(pady=(2, 0))

        # Divider
        tk.Frame(eq_frame, bg=BORDER, width=2).pack(
            side="left", fill="y", pady=4)

        # 10 band columns
        bands_frame = tk.Frame(eq_frame, bg=BG_PANEL)
        bands_frame.pack(side="left", padx=PAD, anchor="s")

        for i, (label, var) in enumerate(zip(BAND_LABELS, self._bands)):
            col = tk.Frame(bands_frame, bg=BG_PANEL)
            col.pack(side="left", padx=6, anchor="s")

            val_lbl = tk.Label(col, text=f"{var.get():+.0f}",
                               font=FONT_TINY, fg=NEON_BLUE, bg=BG_PANEL)
            val_lbl.pack()

            sl = ttk.Scale(col, from_=20, to=-20,
                           variable=var,
                           orient="vertical", length=300,
                           style="EQ.Vertical.TScale",
                           command=lambda v, l=val_lbl, idx=i:
                           (l.config(text=f"{float(v):+.0f}"),
                            self._on_band_change(idx, float(v))))
            sl.pack(pady=2)
            self._sliders.append(sl)

            tk.Label(col, text=label, font=FONT_TINY,
                     fg=TEXT_PRIMARY, bg=BG_PANEL).pack()
        # Zero line hint
        tk.Label(self, text="▲ Boost  |  0dB  |  Cut ▼",
                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_DARK).pack(pady=(0, 4))

        # Apply initial state
        if self._enabled.get():
            self._apply_eq()

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_toggle(self):
        if self._enabled.get():
            self._apply_eq()
        else:
            self._player.eq_disable()
        self._save()

    def _on_preset(self, _event=None):
        name = self._preset_var.get()
        presets = getattr(self._player, 'EQ_PRESETS', {})
        if name not in presets:
            return
        preamp, bands = presets[name]
        self._preamp.set(preamp)
        for i, val in enumerate(bands):
            self._bands[i].set(val)
        # Refresh dB labels
        for col in self._get_band_cols():
            self._refresh_labels(col)
        if self._enabled.get():
            self._apply_eq()

    def _get_band_cols(self):
        """Return band column frames for label refresh."""
        return []   # labels update via slider command callbacks

    def _on_change(self):
        if self._enabled.get():
            self._apply_eq()

    def _on_band_change(self, idx: int, value: float):
        if self._enabled.get():
            self._apply_eq()

    def _apply_eq(self):
        preamp = self._preamp.get()
        bands  = [v.get() for v in self._bands]
        self._player.eq_apply(preamp, bands)

    def _reset(self):
        self._preamp.set(0.0)
        for v in self._bands:
            v.set(0.0)
        self._preset_var.set("Flat")
        if self._enabled.get():
            self._apply_eq()

    def _refresh_labels(self):
        pass   # handled by slider command lambdas

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save(self):
        data = {
            "enabled": self._enabled.get(),
            "preamp":  self._preamp.get(),
            "bands":   [v.get() for v in self._bands],
            "preset":  self._preset_var.get(),
        }
        try:
            with open(EQ_SAVE_PATH, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[EQ] Save error: {e}")

    def _load_saved(self):
        try:
            if not os.path.exists(EQ_SAVE_PATH):
                return
            data = json.load(open(EQ_SAVE_PATH))
            self._enabled.set(data.get("enabled", False))
            self._preamp.set(data.get("preamp", 0.0))
            bands = data.get("bands", [0.0] * 10)
            for i, v in enumerate(bands[:10]):
                self._bands[i].set(v)
            self._preset_var.set(data.get("preset", "Flat"))
        except Exception as e:
            print(f"[EQ] Load error: {e}")

    def refresh(self):
        """Called when page becomes visible."""
        if self._enabled.get():
            self._apply_eq()
