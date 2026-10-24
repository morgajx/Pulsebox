"""
PulseBox Module: Roon Endpoint (RoonBridge)
===========================================
Makes PulseBox a Roon *endpoint* so a Roon Core on your network can play to the
HiFiBerry — it appears in Roon under Audio as a networked zone.

WHY A WRAPPER, NOT A REIMPLEMENTATION?
    "Roon Ready" uses RAAT, a proprietary transport that requires Roon Labs
    certification and a licensed SDK — it cannot be built from open source.
    RoonBridge is Roon's own *free* Linux binary for exactly this purpose, so
    this module installs and manages RoonBridge rather than reinventing it.

REQUIREMENTS
    * A Roon Core (the server) running elsewhere on your LAN.
    * A Roon subscription.
    * RoonBridge installed via scripts/install_roonbridge.sh (or Roon's
      official installer). This module detects and controls that service.

COEXISTENCE
    RoonBridge takes exclusive use of the ALSA device while Roon is playing,
    just like the AirPlay receiver. PulseBox and Roon won't play at the same
    instant; whichever is active holds the DAC.
"""

import shutil
import subprocess
import threading
import tkinter as tk

from core.config import JukeboxModule, APP_VERSION

SERVICE = "roonbridge"


def _service_exists() -> bool:
    try:
        r = subprocess.run(
            ["systemctl", "list-unit-files", f"{SERVICE}.service"],
            capture_output=True, text=True, timeout=5)
        return SERVICE in r.stdout
    except Exception:
        return False


def _service_active() -> bool:
    try:
        r = subprocess.run(["systemctl", "is-active", SERVICE],
                          capture_output=True, text=True, timeout=5)
        return r.stdout.strip() == "active"
    except Exception:
        return False


def _installed() -> bool:
    # RoonBridge installs under /opt/RoonBridge; the service is the reliable tell
    return _service_exists() or bool(shutil.which("RoonBridge")) or \
        _path_exists("/opt/RoonBridge")


def _path_exists(p: str) -> bool:
    import os
    return os.path.exists(p)


class RoonBridgeModule(JukeboxModule):

    MODULE_ID          = "roon_bridge"
    MODULE_NAME        = "Roon Endpoint (RoonBridge)"
    MODULE_VERSION     = APP_VERSION
    MODULE_DESCRIPTION = (
        "Makes PulseBox a Roon endpoint using Roon's official free RoonBridge "
        "binary, so a Roon Core on your network can play to the HiFiBerry. "
        "Requires a Roon Core and subscription.")
    MODULE_AUTHOR      = "PulseBox"

    def __init__(self):
        super().__init__()
        self._status_var = None

    def on_load(self, app):
        super().on_load(app)
        self._refresh_status()

    # ------------------------------------------------------------------

    def _sysctl(self, *args) -> bool:
        """Best-effort systemctl (needs the scoped sudoers rule to work
        without a password)."""
        try:
            r = subprocess.run(["sudo", "-n", "systemctl", *args],
                             capture_output=True, text=True, timeout=15)
            return r.returncode == 0
        except Exception:
            return False

    def _refresh_status(self):
        if not _installed():
            msg = "RoonBridge not installed"
        elif _service_active():
            msg = "Running - visible to Roon Core as an endpoint"
        else:
            msg = "Installed but stopped"
        self._set_status(msg)

    def _set_status(self, msg: str, error: bool = False):
        print(f"[Roon] {'ERROR' if error else 'OK'}: {msg}")
        if self._status_var and self.app:
            try:
                self.app.root.after(0, lambda: self._status_var.set(msg))
            except Exception:
                pass

    def _start(self):
        if not _installed():
            self._set_status("RoonBridge not installed - see install note",
                             error=True)
            return
        self._set_status("Starting...")
        ok = self._sysctl("start", SERVICE)
        self._refresh_status() if ok else self._set_status(
            "Could not start (need passwordless sudo for roonbridge)",
            error=True)

    def _stop(self):
        ok = self._sysctl("stop", SERVICE)
        self._refresh_status() if ok else self._set_status(
            "Could not stop (need passwordless sudo for roonbridge)",
            error=True)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def get_ui_panel(self, parent) -> tk.Frame:
        from ui.theme import (BG_PANEL, BG_CARD, ACCENT, NEON_BLUE, DANGER,
                              TEXT_PRIMARY, TEXT_SECONDARY,
                              FONT_BODY, FONT_SMALL, FONT_TINY)

        frame = tk.Frame(parent, bg=BG_PANEL)
        installed = _installed()

        self._status_var = tk.StringVar(value="...")
        tk.Label(frame, textvariable=self._status_var, font=FONT_BODY,
                 fg=NEON_BLUE, bg=BG_PANEL).pack(anchor="w", pady=(0, 8))
        threading.Thread(target=self._refresh_status, daemon=True).start()

        if installed:
            row = tk.Frame(frame, bg=BG_PANEL)
            row.pack(anchor="w", pady=(0, 8))
            tk.Button(row, text="Start", font=FONT_SMALL, fg=BG_PANEL,
                      bg=ACCENT, relief="flat", bd=0, cursor="hand2",
                      padx=14, pady=5,
                      command=lambda: threading.Thread(
                          target=self._start, daemon=True).start()
                      ).pack(side="left", padx=(0, 6))
            tk.Button(row, text="Stop", font=FONT_SMALL, fg=TEXT_PRIMARY,
                      bg=BG_CARD, relief="flat", bd=0, cursor="hand2",
                      padx=14, pady=5,
                      command=lambda: threading.Thread(
                          target=self._stop, daemon=True).start()
                      ).pack(side="left")
        else:
            tk.Label(frame,
                     text="RoonBridge isn't installed yet. Install Roon's\n"
                          "official free endpoint (one-time), then reopen this:\n"
                          "    bash scripts/install_roonbridge.sh",
                     font=FONT_TINY, fg=DANGER, bg=BG_PANEL,
                     justify="left").pack(anchor="w", pady=(0, 8))

        tk.Label(frame,
                 text="Roon Ready uses a proprietary, certified-only protocol, "
                      "so this uses Roon's official free RoonBridge binary "
                      "instead. You need a Roon Core running elsewhere on your "
                      "network and a Roon subscription. Once RoonBridge is "
                      "running, open Roon > Settings > Audio and enable this "
                      "device; audio plays through your HiFiBerry.",
                 font=FONT_TINY, fg=TEXT_SECONDARY, bg=BG_PANEL,
                 wraplength=460, justify="left").pack(anchor="w", pady=(10, 0))

        return frame
