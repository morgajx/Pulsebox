"""
PulseBox Module: Rotary Encoder Volume Control
================================================
Controls PulseBox volume via a physical rotary encoder connected to
the Raspberry Pi GPIO pins.

WIRING:
  Encoder pin A (CLK) → GPIO pin (default: 17)
  Encoder pin B (DT)  → GPIO pin (default: 18)
  Encoder pin C (GND) → any GND pin
  Button pin  (SW)    → GPIO pin (default: 27) [optional]

The push button (if wired) can mute/unmute or reset to default volume.

SUPPORTED HARDWARE:
  Any standard KY-040 or EC11 incremental rotary encoder.

Pi 5 NOTE:
  The Pi 5 uses the RP1 GPIO controller at /dev/gpiochip4.
  This module detects the Pi model automatically and sets the correct
  lgpio chip number. No manual config needed.

INSTALL:
  sudo apt install python3-gpiozero python3-lgpio -y
  # or:
  pip install gpiozero lgpio --break-system-packages
"""

import os
import threading
import time
import tkinter as tk

from core.config import JukeboxModule, APP_VERSION

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_CLK_PIN    = 17    # encoder A (CLK)
DEFAULT_DT_PIN     = 18    # encoder B (DT)
DEFAULT_BTN_PIN    = 27    # push button (SW) — 0 to disable
DEFAULT_STEP       = 2     # volume change per detent
DEFAULT_BTN_ACTION = "mute"  # "mute" or "reset"
DEFAULT_RESET_VOL  = 80


# ---------------------------------------------------------------------------
# Pi 5 detection — sets correct gpiochip number
# ---------------------------------------------------------------------------

def _lgpio_chip() -> int:
    """Return the correct lgpio chip number for this Pi model."""
    try:
        with open("/proc/device-tree/model", "r") as f:
            model = f.read()
        if "Raspberry Pi 5" in model:
            return 4   # RP1 controller on Pi 5
    except Exception:
        pass
    return 0  # Pi 4 and earlier


def _gpiozero_available() -> bool:
    try:
        import gpiozero  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------

class RotaryEncoderVolume(JukeboxModule):
    MODULE_ID          = "rotary_encoder_volume"
    MODULE_NAME        = "Rotary Encoder Volume"
    MODULE_VERSION = APP_VERSION
    MODULE_DESCRIPTION = (
        "Controls PulseBox volume via a physical rotary encoder on the GPIO pins. "
        "Supports KY-040 / EC11 encoders. Push button can mute or reset volume. "
        "Works on Pi 4 and Pi 5 (auto-detects RP1 GPIO chip)."
    )
    MODULE_AUTHOR = "Andrew J. Morgan"

    def __init__(self):
        super().__init__()
        self._encoder   = None
        self._button    = None
        self._running   = False
        self._muted     = False
        self._pre_mute  = 80

        # UI vars
        self._status_var = None
        self._vol_var    = None
        self._clk_var    = None
        self._dt_var     = None
        self._btn_var    = None
        self._step_var   = None
        self._action_var = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_load(self, app):
        super().on_load(app)
        if _gpiozero_available() and self.config.get("encoder_enabled", False):
            threading.Thread(target=self._start_encoder, daemon=True).start()

    def on_unload(self):
        self._stop_encoder()
        super().on_unload()

    # ------------------------------------------------------------------
    # Encoder control
    # ------------------------------------------------------------------

    def _start_encoder(self):
        clk  = self.config.get("encoder_clk",  DEFAULT_CLK_PIN)
        dt   = self.config.get("encoder_dt",   DEFAULT_DT_PIN)
        btn  = self.config.get("encoder_btn",  DEFAULT_BTN_PIN)
        chip = _lgpio_chip()

        try:
            from gpiozero import RotaryEncoder, Button
            from gpiozero.pins.lgpio import LGPIOFactory
            from gpiozero import Device
            Device.pin_factory = LGPIOFactory(chip=chip)

            self._encoder = RotaryEncoder(clk, dt, max_steps=0)
            self._encoder.when_rotated_clockwise      = self._on_cw
            self._encoder.when_rotated_counter_clockwise = self._on_ccw

            if btn and btn > 0:
                self._button = Button(btn, bounce_time=0.1)
                self._button.when_pressed = self._on_button

            self._running = True
            self._set_status(f"Active — CLK={clk} DT={dt}"
                             + (f" BTN={btn}" if btn else "") +
                             f" (chip={chip})")

        except Exception as e:
            self._set_status(f"Error: {e}", error=True)
            print(f"[RotaryEncoder] Failed to start: {e}")

    def _stop_encoder(self):
        self._running = False
        try:
            if self._encoder:
                self._encoder.close()
                self._encoder = None
        except Exception:
            pass
        try:
            if self._button:
                self._button.close()
                self._button = None
        except Exception:
            pass
        self._set_status("Stopped")

    # ------------------------------------------------------------------
    # Encoder callbacks — run in gpiozero background thread
    # ------------------------------------------------------------------

    def _on_cw(self):
        """Clockwise — volume up."""
        if not self.app:
            return
        step = self.config.get("encoder_step", DEFAULT_STEP)
        vol  = self.app.player.get_volume()
        new  = min(100, vol + step)
        self.app.player.set_volume(new)
        self._muted = False
        if self.app:
            self.app.root.after(0, lambda v=new: self._update_vol_ui(v))

    def _on_ccw(self):
        """Counter-clockwise — volume down."""
        if not self.app:
            return
        step = self.config.get("encoder_step", DEFAULT_STEP)
        vol  = self.app.player.get_volume()
        new  = max(0, vol - step)
        self.app.player.set_volume(new)
        if new == 0:
            self._muted = True
        if self.app:
            self.app.root.after(0, lambda v=new: self._update_vol_ui(v))

    def _on_button(self):
        """Button press — mute/unmute or reset volume."""
        if not self.app:
            return
        action = self.config.get("encoder_btn_action", DEFAULT_BTN_ACTION)
        if action == "mute":
            if self._muted:
                # Unmute — restore previous volume
                self.app.player.set_volume(self._pre_mute)
                self._muted = False
                self.app.root.after(0, lambda: self._update_vol_ui(self._pre_mute))
            else:
                # Mute
                self._pre_mute = self.app.player.get_volume()
                self.app.player.set_volume(0)
                self._muted = True
                self.app.root.after(0, lambda: self._update_vol_ui(0))
        elif action == "reset":
            reset_vol = self.config.get("encoder_reset_vol", DEFAULT_RESET_VOL)
            self.app.player.set_volume(reset_vol)
            self._muted = False
            self.app.root.after(0, lambda v=reset_vol: self._update_vol_ui(v))

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------

    def _set_status(self, msg: str, error: bool = False):
        prefix = "ERROR" if error else "OK"
        print(f"[RotaryEncoder] {prefix}: {msg}")
        if self._status_var and self.app:
            self.app.root.after(0, lambda: self._status_var.set(msg))

    def _update_vol_ui(self, vol: int):
        # Encoder callbacks run on the gpiozero background thread, so any
        # Tkinter update must be marshalled onto the main thread.
        if not self.app:
            return
        root = getattr(self.app, "root", None)

        def _apply(v=vol):
            try:
                if self._vol_var:
                    self._vol_var.set(f"{v}%")
            except Exception:
                pass
            pb = getattr(self.app, "_player_bar", None)
            if pb is not None and hasattr(pb, "update_volume"):
                try:
                    pb.update_volume(v)
                except Exception:
                    pass

        if root is not None:
            root.after(0, _apply)
        else:
            _apply()

    def _save_and_restart(self):
        """Save config from UI fields and restart encoder."""
        self._stop_encoder()

        self.config.set("encoder_clk",        int(self._clk_var.get()))
        self.config.set("encoder_dt",         int(self._dt_var.get()))
        self.config.set("encoder_btn",        int(self._btn_var.get()))
        self.config.set("encoder_step",       int(self._step_var.get()))
        self.config.set("encoder_btn_action", self._action_var.get())
        self.config.set("encoder_enabled",    True)

        threading.Thread(target=self._start_encoder, daemon=True).start()
        self.emit_notification("Rotary encoder started", "success")

    def _toggle_enabled(self, enabled: bool):
        self.config.set("encoder_enabled", enabled)
        if enabled:
            threading.Thread(target=self._start_encoder, daemon=True).start()
        else:
            self._stop_encoder()

    # ------------------------------------------------------------------
    # UI Panel
    # ------------------------------------------------------------------

    def get_ui_panel(self, parent) -> tk.Frame:
        from ui.theme import (BG_PANEL, BG_CARD, BG_DARK, ACCENT, SUCCESS,
                               DANGER, WARNING, NEON_BLUE,
                               TEXT_PRIMARY, TEXT_SECONDARY, TEXT_DIM,
                               FONT_BODY, FONT_SMALL, FONT_TINY,
                               FONT_SUBHEAD, PAD, PAD_SM, BORDER)

        outer = tk.Frame(parent, bg=BG_PANEL)

        # ── Install warning ───────────────────────────────────────────
        if not _gpiozero_available():
            tk.Label(outer, text="gpiozero not installed",
                     font=FONT_SUBHEAD, fg=DANGER, bg=BG_PANEL).pack(pady=8)
            tk.Label(outer, text="Install with:",
                     font=FONT_BODY, fg=TEXT_PRIMARY, bg=BG_PANEL).pack()
            tk.Label(outer,
                     text="sudo apt install python3-gpiozero python3-lgpio -y",
                     font=("Courier", 10), fg=NEON_BLUE,
                     bg=BG_DARK, padx=8, pady=4).pack(
                fill="x", padx=PAD, pady=4)
            return outer

        def _row(label: str) -> tk.Frame:
            row = tk.Frame(outer, bg=BG_PANEL)
            row.pack(fill="x", padx=PAD, pady=2)
            tk.Label(row, text=label, font=FONT_BODY, fg=TEXT_PRIMARY,
                     bg=BG_PANEL, width=16, anchor="w").pack(side="left")
            return row

        def _spin(parent, var, lo, hi, width=6):
            return tk.Spinbox(parent, textvariable=var, from_=lo, to=hi,
                              width=width, font=FONT_BODY,
                              bg=BG_CARD, fg=TEXT_PRIMARY,
                              buttonbackground=BG_CARD,
                              relief="flat", insertbackground=ACCENT,
                              highlightthickness=1,
                              highlightbackground=BORDER,
                              highlightcolor=ACCENT)

        # ── Status ────────────────────────────────────────────────────
        self._status_var = tk.StringVar(
            value="Stopped" if not self._running else "Active")
        tk.Label(outer, textvariable=self._status_var,
                 font=FONT_TINY, fg=NEON_BLUE,
                 bg=BG_PANEL, anchor="w").pack(
            fill="x", padx=PAD, pady=(6, 2))

        # ── Current volume readout ────────────────────────────────────
        vol = self.app.player.get_volume() if self.app else 0
        self._vol_var = tk.StringVar(value=f"{vol}%")
        vrow = tk.Frame(outer, bg=BG_PANEL)
        vrow.pack(fill="x", padx=PAD, pady=(0, 6))
        tk.Label(vrow, text="Current volume:",
                 font=FONT_BODY, fg=TEXT_PRIMARY,
                 bg=BG_PANEL, width=16, anchor="w").pack(side="left")
        tk.Label(vrow, textvariable=self._vol_var,
                 font=FONT_SUBHEAD, fg=ACCENT,
                 bg=BG_PANEL).pack(side="left")

        tk.Frame(outer, bg=BORDER, height=1).pack(
            fill="x", padx=PAD, pady=(0, 6))

        # ── GPIO pin config ───────────────────────────────────────────
        self._clk_var  = tk.IntVar(value=self.config.get("encoder_clk",  DEFAULT_CLK_PIN))
        self._dt_var   = tk.IntVar(value=self.config.get("encoder_dt",   DEFAULT_DT_PIN))
        self._btn_var  = tk.IntVar(value=self.config.get("encoder_btn",  DEFAULT_BTN_PIN))
        self._step_var = tk.IntVar(value=self.config.get("encoder_step", DEFAULT_STEP))

        r1 = _row("CLK pin (A):")
        _spin(r1, self._clk_var, 1, 27).pack(side="left", padx=(0, 8))
        tk.Label(r1, text="GPIO pin for encoder CLK / A signal",
                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_PANEL).pack(side="left")

        r2 = _row("DT pin (B):")
        _spin(r2, self._dt_var, 1, 27).pack(side="left", padx=(0, 8))
        tk.Label(r2, text="GPIO pin for encoder DT / B signal",
                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_PANEL).pack(side="left")

        r3 = _row("Button pin (SW):")
        _spin(r3, self._btn_var, 0, 27).pack(side="left", padx=(0, 8))
        tk.Label(r3, text="GPIO pin for push button (0 = disabled)",
                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_PANEL).pack(side="left")

        r4 = _row("Step size:")
        _spin(r4, self._step_var, 1, 10).pack(side="left", padx=(0, 8))
        tk.Label(r4, text="Volume change per detent (1-10)",
                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_PANEL).pack(side="left")

        # ── Button action ─────────────────────────────────────────────
        r5 = _row("Button action:")
        self._action_var = tk.StringVar(
            value=self.config.get("encoder_btn_action", DEFAULT_BTN_ACTION))
        for val, label in [("mute", "Mute / Unmute"),
                           ("reset", f"Reset to {DEFAULT_RESET_VOL}%")]:
            tk.Radiobutton(r5, text=label,
                           variable=self._action_var, value=val,
                           font=FONT_SMALL, fg=TEXT_PRIMARY,
                           bg=BG_PANEL, selectcolor=BG_CARD,
                           activebackground=BG_PANEL,
                           cursor="hand2").pack(side="left", padx=(0, 12))

        # ── Pi model info ─────────────────────────────────────────────
        chip = _lgpio_chip()
        chip_msg = (f"Pi 5 detected — using gpiochip{chip} (RP1)"
                    if chip == 4 else
                    f"Pi 4/earlier detected — using gpiochip{chip}")
        tk.Label(outer, text=chip_msg,
                 font=FONT_TINY, fg=SUCCESS if chip == 4 else TEXT_PRIMARY,
                 bg=BG_PANEL, anchor="w").pack(
            fill="x", padx=PAD, pady=(4, 0))

        # ── Wiring diagram ────────────────────────────────────────────
        wiring = tk.Frame(outer, bg=BG_CARD,
                          highlightthickness=1,
                          highlightbackground=BORDER)
        wiring.pack(fill="x", padx=PAD, pady=6)
        tk.Label(wiring, text="WIRING",
                 font=FONT_TINY, fg=ACCENT,
                 bg=BG_CARD).pack(anchor="w", padx=8, pady=(4, 0))
        tk.Label(wiring,
                 text=(f"  CLK (A)  →  GPIO {self._clk_var.get()}\n"
                       f"  DT  (B)  →  GPIO {self._dt_var.get()}\n"
                       f"  GND (C)  →  any GND pin\n"
                       f"  SW  (btn) →  GPIO {self._btn_var.get()}"
                       + (" (disabled)" if self._btn_var.get() == 0 else "")),
                 font=("Courier", 10), fg=TEXT_PRIMARY,
                 bg=BG_CARD, justify="left").pack(
            anchor="w", padx=8, pady=(2, 6))

        def _refresh_wiring(*_):
            # Refresh wiring label when pins change
            for w in wiring.winfo_children():
                if isinstance(w, tk.Label) and "CLK" in (w.cget("text") or ""):
                    w.config(text=(
                        f"  CLK (A)  →  GPIO {self._clk_var.get()}\n"
                        f"  DT  (B)  →  GPIO {self._dt_var.get()}\n"
                        f"  GND (C)  →  any GND pin\n"
                        f"  SW  (btn) →  GPIO {self._btn_var.get()}"
                        + (" (disabled)" if self._btn_var.get() == 0 else "")))

        self._clk_var.trace_add("write", _refresh_wiring)
        self._dt_var.trace_add("write",  _refresh_wiring)
        self._btn_var.trace_add("write", _refresh_wiring)

        # ── Buttons ───────────────────────────────────────────────────
        btn_row = tk.Frame(outer, bg=BG_PANEL)
        btn_row.pack(fill="x", padx=PAD, pady=(4, PAD))

        tk.Button(btn_row, text="▶  Start / Restart",
                  font=FONT_SMALL, fg=BG_PANEL, bg=SUCCESS,
                  relief="flat", bd=0, cursor="hand2",
                  padx=14, pady=6,
                  command=self._save_and_restart).pack(side="left", padx=(0, 8))

        tk.Button(btn_row, text="⏹  Stop",
                  font=FONT_SMALL, fg=DANGER, bg=BG_CARD,
                  relief="flat", bd=0, cursor="hand2",
                  padx=14, pady=6,
                  command=self._stop_encoder).pack(side="left", padx=(0, 8))

        # Test buttons
        tk.Button(btn_row, text="+ Vol",
                  font=FONT_SMALL, fg=BG_PANEL, bg=ACCENT,
                  relief="flat", bd=0, cursor="hand2",
                  padx=10, pady=6,
                  command=self._on_cw).pack(side="left", padx=(0, 4))

        tk.Button(btn_row, text="- Vol",
                  font=FONT_SMALL, fg=BG_PANEL, bg=ACCENT,
                  relief="flat", bd=0, cursor="hand2",
                  padx=10, pady=6,
                  command=self._on_ccw).pack(side="left", padx=(0, 8))

        tk.Label(btn_row, text="← test without encoder",
                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_PANEL).pack(side="left")

        return outer
