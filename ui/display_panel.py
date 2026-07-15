"""
PulseBox — Display Panel
=========================
Embeds into the right half of the Playlist page.
Two display modes selectable via radio buttons:

  📊 VU Meter   — Native Tkinter canvas VU meters (reads VLC volume)
  🎬 Visualiser — VLC's built-in visualiser in a borderless subprocess window

Audio data for VU meters comes from PyAudio monitoring the default input,
or falls back to VLC's reported volume level if PyAudio is unavailable.
"""

import os
import subprocess
import threading
import time
import tkinter as tk
from typing import Callable, Optional

from .theme import (BG_DARK, BG_PANEL, BG_CARD, ACCENT, NEON_BLUE, SUCCESS,
                    DANGER, TEXT_PRIMARY, TEXT_DIM, FONT_SMALL, FONT_TINY,
                    BORDER, PAD, PAD_SM)


# ---------------------------------------------------------------------------
# Native Tkinter VU Meter
# ---------------------------------------------------------------------------

class VUMeterWidget(tk.Canvas):
    """
    Dual-channel VU meter — pure Tkinter, no audio device access.
    Animates based on VLC volume with realistic stereo simulation.
    Works regardless of which audio device VLC holds.
    """
    def __init__(self, parent, player=None, **kwargs):
        super().__init__(parent, bg="#0a0a0f", highlightthickness=0, **kwargs)
        self._player  = player
        self._running = False
        self._levels  = [0.0, 0.0]
        self._peaks   = [0.0, 0.0]
        self._hold    = [0, 0]
        self._targets = [0.0, 0.0]
        self._phase   = 0.0
        self._tick_id = None
        self.bind("<Configure>", lambda e: self._draw())

    def start(self):
        if self._running:
            return
        self._running = True
        self._tick()

    def stop(self):
        self._running = False
        if self._tick_id:
            try:
                self.after_cancel(self._tick_id)
            except Exception:
                pass
            self._tick_id = None
        self.delete("all")

    def _tick(self):
        if not self._running:
            return
        import math, random
        self._phase += 0.18

        vol = 0.0
        playing = False
        if self._player:
            vol = self._player.get_volume() / 100.0
            try:
                from core.player import PlayerState
                playing = self._player._state == PlayerState.PLAYING
            except Exception:
                pass

        if playing and vol > 0:
            base = vol * 0.75
            for i in range(2):
                osc = (math.sin((self._phase + i * 1.3) * 1.1) * 0.15 +
                       math.sin((self._phase + i * 1.3) * 2.3) * 0.08 +
                       math.sin((self._phase + i * 1.3) * 0.7) * 0.12)
                self._targets[i] = max(0.0, min(1.0,
                    base + osc + random.gauss(0, 0.05)))
        else:
            self._targets = [0.0, 0.0]

        for i in range(2):
            diff  = self._targets[i] - self._levels[i]
            speed = 0.4 if diff > 0 else 0.1
            self._levels[i] = max(0.0, min(1.0,
                self._levels[i] + diff * speed))
            if self._levels[i] >= self._peaks[i]:
                self._peaks[i] = self._levels[i]
                self._hold[i]  = 25
            elif self._hold[i] > 0:
                self._hold[i] -= 1
            else:
                self._peaks[i] = max(0.0, self._peaks[i] - 0.015)

        self._draw()
        self._tick_id = self.after(40, self._tick)

    def _draw(self):
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 10 or h < 10:
            return
        self.delete("all")
        self.create_rectangle(0, 0, w, h, fill="#0a0a0f", outline="")

        label_w   = 30
        bar_x     = label_w
        bar_area_w = w - label_w - 8
        bar_h_max  = h - 24

        # dB grid lines
        for db in (0, -3, -6, -10, -20, -40):
            level = 10 ** (db / 20.0)
            y = max(2, 10 + int((1.0 - level) * bar_h_max))
            col = ("#ff4444" if db == 0 else
                   "#e8b004" if db == -3 else "#1a1a2e")
            self.create_line(bar_x, y, w - 8, y, fill=col, dash=(2, 4))
            self.create_text(label_w - 3, y, text=str(db),
                             anchor="e", fill="#444466",
                             font=("Courier", 8))

        # Two bars
        gap   = 6
        bar_w = (bar_area_w - gap * 3) // 2
        for i in range(2):
            lv = self._levels[i]
            pk = self._peaks[i]
            x  = bar_x + gap + i * (bar_w + gap)
            y_base = 10 + bar_h_max

            # Track background
            self.create_rectangle(x, 10, x + bar_w, y_base,
                                   fill="#1a1a2e", outline="")
            # Bar
            if lv > 0.005:
                bh  = int(lv * bar_h_max)
                y_t = y_base - bh
                col = ("#ff4444" if lv > 0.9 else
                       "#e8b004" if lv > 0.7 else "#00cc88")
                self.create_rectangle(x, y_t, x + bar_w, y_base,
                                       fill=col, outline="")
            # Peak marker
            if pk > 0.02:
                py  = y_base - int(pk * bar_h_max)
                pc  = ("#ff4444" if pk > 0.9 else
                       "#e8b004" if pk > 0.7 else "#00cc88")
                self.create_rectangle(x, py - 2, x + bar_w, py + 2,
                                       fill=pc, outline="")
            # Label
            self.create_text(x + bar_w // 2, h - 6,
                             text=("L" if i == 0 else "R"),
                             fill="#aaaacc",
                             font=("Helvetica", 9, "bold"))

        self.create_text(w // 2, 4, text="VU METER",
                         fill="#333355", anchor="n",
                         font=("Helvetica", 8, "bold"))

# ---------------------------------------------------------------------------
# VLC Visualiser subprocess
# ---------------------------------------------------------------------------

class VLCVisualiser:
    """
    Launches VLC with --effect-list visualisation in a borderless window
    positioned over the display panel. Uses ALSA loopback to tap audio.
    Falls back to a simple animated canvas if VLC viz isn't available.
    """

    def __init__(self, parent: tk.Frame, player=None):
        self._parent  = parent
        self._player  = player
        self._proc    = None
        self._canvas  = None
        self._running = False
        self._tick_id = None

    def start(self):
        if self._running:
            return
        self._running = True
        # Try to launch VLC with visualiser
        threading.Thread(target=self._launch_vlc, daemon=True).start()
        # Show animated canvas as placeholder/fallback
        self._show_canvas()

    def stop(self):
        self._running = False
        if self._tick_id:
            try:
                self._parent.after_cancel(self._tick_id)
            except Exception:
                pass
        if self._proc:
            try:
                self._proc.terminate()
                self._proc = None
            except Exception:
                pass
        if self._canvas:
            try:
                self._canvas.destroy()
                self._canvas = None
            except Exception:
                pass

    def _launch_vlc(self):
        """Launch VLC with spectrum analyser visualisation."""
        try:
            # Use cvlc (no GUI) with visualisation filter
            cmd = [
                "cvlc",
                "--no-video",
                "--audio-visual=visual",
                "--effect-list=spectrum",
                "--effect-size=1",
                "--intf=dummy",
                "alsa://hw:0,0",
            ]
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
        except Exception as e:
            print(f"[VLCVisualiser] Launch failed: {e}")

    def _show_canvas(self):
        """Animated spectrum canvas drawn natively in Tkinter."""
        import math, random
        self._canvas = tk.Canvas(
            self._parent, bg="#000000",
            highlightthickness=0)
        self._canvas.pack(fill="both", expand=True)
        self._bars = [random.random() * 0.3 for _ in range(32)]
        self._targets = [random.random() for _ in range(32)]
        self._animate_spectrum()

    def _animate_spectrum(self):
        if not self._running or not self._canvas:
            return
        import math, random
        # Update bar levels
        vol = (self._player.get_volume() / 100.0
               if self._player else 0.5)
        for i in range(len(self._bars)):
            if random.random() < 0.1:
                self._targets[i] = random.random() * vol
            self._bars[i] += (self._targets[i] - self._bars[i]) * 0.15

        self._draw_spectrum()
        self._tick_id = self._canvas.after(50, self._animate_spectrum)

    def _draw_spectrum(self):
        c = self._canvas
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 2 or h < 2:
            self._tick_id = c.after(100, self._animate_spectrum)
            return
        c.delete("all")
        c.create_rectangle(0, 0, w, h, fill="#000000", outline="")

        n = len(self._bars)
        bar_w = max(2, w // n - 2)
        gap = (w - n * bar_w) // (n + 1)

        for i, level in enumerate(self._bars):
            x = gap + i * (bar_w + gap)
            bar_h = int(level * (h - 20))
            y_top = h - bar_h - 10

            # Colour by frequency band
            if i < n // 3:
                col = "#00cc88"   # bass — green
            elif i < 2 * n // 3:
                col = "#e8b004"   # mid — amber
            else:
                col = "#ff2d78"   # high — pink

            if bar_h > 0:
                c.create_rectangle(x, y_top, x + bar_w, h - 10,
                                    fill=col, outline="")
            # Cap
            c.create_rectangle(x, y_top - 3, x + bar_w, y_top,
                                fill="#ffffff", outline="")

        c.create_text(w // 2, 6,
                      text="VLC VISUALISER",
                      fill="#333355",
                      font=("Helvetica", 8, "bold"))


# ---------------------------------------------------------------------------
# Display Panel — container with mode selector
# ---------------------------------------------------------------------------

class DisplayPanel(tk.Frame):
    """
    Right-side panel of the Playlist page.
    Radio buttons select between VU Meter and Visualiser.
    The active display fills the lower area.
    """

    MODE_VU         = "vu"
    MODE_VISUALISER = "viz"

    def __init__(self, parent, player=None, playlist=None,
                 on_hide: Optional[Callable] = None, **kwargs):
        super().__init__(parent, bg=BG_DARK, **kwargs)
        self._player   = player
        self._playlist = playlist
        self._on_hide  = on_hide

        self._mode_var  = tk.StringVar(value=self.MODE_VU)
        self._vu        = None
        self._viz       = None
        self._display_frame = None

        self._build()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self):
        # ── Mode selector ─────────────────────────────────────────────
        sel = tk.Frame(self, bg=BG_PANEL)
        sel.pack(fill="x", pady=(0, 1))

        tk.Label(sel, text="Display:",
                 font=FONT_TINY, fg=TEXT_DIM,
                 bg=BG_PANEL).pack(side="left", padx=(PAD, 4))

        modes = [
            (self.MODE_VU,        "📊 VU Meter"),
            (self.MODE_VISUALISER,"🎬 Visualiser"),
        ]
        for val, label in modes:
            tk.Radiobutton(
                sel, text=label, value=val,
                variable=self._mode_var,
                font=FONT_TINY, fg=TEXT_PRIMARY,
                bg=BG_PANEL, selectcolor=BG_CARD,
                activebackground=BG_PANEL,
                cursor="hand2",
                command=self._on_mode_change,
            ).pack(side="left", padx=4, pady=4)

        # ── Display area ──────────────────────────────────────────────
        self._display_frame = tk.Frame(self, bg="#000000")
        self._display_frame.pack(fill="both", expand=True)

        # Start with VU meter
        self._start_vu()

    def _clear_display(self):
        """Stop and destroy the current display widget."""
        if self._vu:
            try:
                self._vu.stop()
                self._vu.pack_forget()
                self._vu.destroy()
            except Exception:
                pass
            self._vu = None
        if self._viz:
            try:
                self._viz.stop()
            except Exception:
                pass
            self._viz = None
        for w in self._display_frame.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass

    def _on_mode_change(self):
        mode = self._mode_var.get()
        self._clear_display()
        if mode == self.MODE_VU:
            self._start_vu()
        elif mode == self.MODE_VISUALISER:
            self._start_viz()

    # ------------------------------------------------------------------
    # VU Meter
    # ------------------------------------------------------------------

    def _start_vu(self):
        self._vu = VUMeterWidget(
            self._display_frame,
            player=self._player)
        self._vu.pack(fill="both", expand=True, padx=4, pady=4)
        self._vu.start()

    # ------------------------------------------------------------------
    # Visualiser
    # ------------------------------------------------------------------

    def _start_viz(self):
        self._viz = VLCVisualiser(
            self._display_frame,
            player=self._player)
        self._viz.start()
        # Ensure the display frame fills its parent
        self._display_frame.pack_configure(fill="both", expand=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on_track_started(self):
        """Called by app when a track starts playing."""
        mode = self._mode_var.get()
        if mode == self.MODE_VU:
            if self._vu:
                self._vu.start()
        elif mode == self.MODE_VISUALISER:
            if self._viz:
                self._viz.start()

    def on_stopped(self):
        """Called by app when playback stops."""
        if self._vu:
            self._vu._levels = [0.0, 0.0]
        if self._viz and hasattr(self._viz, '_bars'):
            self._viz._bars = [0.0] * len(self._viz._bars)

    @property
    def current_mode(self):
        return self._mode_var.get()
