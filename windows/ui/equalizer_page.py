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


class CanvasFader(tk.Canvas):
    """
    A vertical fader drawn on a Canvas so its bar width is exact — unlike
    ttk.Scale, whose trough thickness is theme-locked. Drop-in enough for the
    EQ page: it's bound to a tk variable, fires ``command(value)`` on every
    change, and supports click/drag to set the value.
    """

    def __init__(self, parent, variable, from_=20.0, to=-20.0,
                 length=450, width=40, command=None,
                 trough="#101018", fill="#28c8ff", handle="#f0f0f8",
                 bg="#0a0a12"):
        super().__init__(parent, width=width, height=length,
                         bg=bg, highlightthickness=0, cursor="hand2")
        self._var = variable
        self._from = float(from_)          # value at the top
        self._to = float(to)               # value at the bottom
        self._len = int(length)
        self._cw = int(width)              # NOT self._w — tkinter owns that
        self._command = command
        self._trough = trough
        self._fill = fill
        self._handle = handle

        self.bind("<Button-1>", self._on_drag)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<Configure>", lambda e: self._redraw())
        self._trace = self._var.trace_add("write", lambda *_: self._changed())
        self._redraw()

    # value <-> y-pixel mapping
    def _val_to_y(self, val: float) -> int:
        span = self._from - self._to
        frac = (self._from - val) / span if span else 0.5
        return int(max(0.0, min(1.0, frac)) * self._len)

    def _y_to_val(self, y: float) -> float:
        frac = max(0.0, min(1.0, y / self._len))
        return self._from - frac * (self._from - self._to)

    def _on_drag(self, event):
        val = round(self._y_to_val(event.y))
        val = max(min(self._from, self._to) + 0,
                  min(max(self._from, self._to), val))
        if val != self._var.get():
            self._var.set(val)          # trace -> _changed -> redraw + command
        else:
            self._redraw()

    def _changed(self):
        self._redraw()
        if self._command:
            try:
                self._command(self._var.get())
            except Exception:
                pass

    def _redraw(self):
        self.delete("all")
        w, L = self._cw, self._len
        cx = w // 2
        bar_w = max(12, w - 8)           # the visible bar — full, controllable
        x0, x1 = cx - bar_w // 2, cx + bar_w // 2

        # trough
        self.create_rectangle(x0, 2, x1, L - 2, fill=self._trough,
                              outline="", width=0)
        # zero line
        yz = self._val_to_y(0.0)
        self.create_line(x0, yz, x1, yz, fill="#3a3a48")
        # fill from zero to current value
        try:
            val = float(self._var.get())
        except Exception:
            val = 0.0
        yv = self._val_to_y(val)
        top, bot = (yv, yz) if yv < yz else (yz, yv)
        colour = self._fill if val >= 0 else "#ff7043"   # boost vs cut
        if abs(yv - yz) > 1:
            self.create_rectangle(x0, top, x1, bot, fill=colour,
                                  outline="", width=0)
        # handle
        self.create_rectangle(x0 - 2, yv - 4, x1 + 2, yv + 4,
                              fill=self._handle, outline="", width=0)

    def set(self, value):
        self._var.set(value)

    def get(self):
        return self._var.get()


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

        # Band sliders are custom CanvasFaders (see below) for exact bar width.

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

        preamp_sl = CanvasFader(preamp_col, self._preamp,
                                from_=20, to=-20, length=450, width=44,
                                fill=ACCENT, bg=BG_PANEL, trough=BG_DARK,
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
            col.pack(side="left", padx=12, anchor="s")

            val_lbl = tk.Label(col, text=f"{var.get():+.0f}",
                               font=FONT_TINY, fg=NEON_BLUE, bg=BG_PANEL)
            val_lbl.pack()

            sl = CanvasFader(col, var, from_=20, to=-20,
                             length=450, width=44,
                             fill=NEON_BLUE, bg=BG_PANEL, trough=BG_DARK,
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
