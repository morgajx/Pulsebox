"""
PulseBox Module: UPnP/DLNA Media Renderer  ("cast to PulseBox")
===============================================================
Makes PulseBox appear on the network as a media *renderer*, so Android phones
(and any UPnP controller) can push audio to it and play through the HiFiBerry.

WHY NOT CHROMECAST?
    Google Cast is a proprietary, licensed protocol — only certified hardware
    may act as a Cast receiver, so the native Android "Cast" button can never
    see a Raspberry Pi. UPnP/DLNA is the open equivalent and is what Android
    casting apps use. Use an app such as BubbleUPnP, Hi-Fi Cast or VLC, and
    pick "PulseBox" as the renderer.

This wraps **gmediarender** (GStreamer UPnP renderer), run as a per-user
daemon so no root is needed at run time. Install it once with:

    sudo apt install gmediarender

Note this is the counterpart to the DLNA Media Server module: the server
*shares* PulseBox's library to other devices; this renderer lets other devices
*play through* PulseBox.
"""

import os
import shutil
import signal
import socket
import subprocess
import threading
import time
import tkinter as tk

from core.config import JukeboxModule, APP_VERSION

DEFAULT_NAME = "PulseBox"
DEFAULT_PORT = 49494


def _gmediarender_bin() -> str:
    for name in ("gmediarender", "gmediarenderer"):
        p = shutil.which(name)
        if p:
            return p
    return ""


def _alsa_device() -> str:
    """Match the DAC PulseBox itself uses, so casting hits the HiFiBerry."""
    try:
        import re
        out = subprocess.run(["aplay", "-l"], capture_output=True,
                             text=True, timeout=5).stdout
        for line in out.splitlines():
            m = re.match(r"card (\d+): (\S+)", line)
            if not m:
                continue
            if re.search(r"hifiberry|iqaudio|dacplus|audioinjector|allo|"
                         r"justboom", line, re.I) and \
               not re.search(r"hdmi|vc4|bcm2835", line, re.I):
                return f"plughw:CARD={m.group(2)}"
    except Exception:
        pass
    return ""


class DLNARendererModule(JukeboxModule):

    MODULE_ID          = "dlna_renderer"
    MODULE_NAME        = "Cast to PulseBox (UPnP Renderer)"
    MODULE_VERSION     = APP_VERSION
    MODULE_DESCRIPTION = (
        "Lets Android phones and other devices cast audio to PulseBox over "
        "UPnP/DLNA (the open equivalent of Chromecast, which is proprietary). "
        "Requires the 'gmediarender' package.")
    MODULE_AUTHOR      = "PulseBox"

    def __init__(self):
        super().__init__()
        self._proc        = None
        self._status_var  = None
        self._name_var    = None
        self._enabled_var = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_load(self, app):
        super().on_load(app)
        if self.config.get("renderer_enabled", False) and _gmediarender_bin():
            threading.Thread(target=self._start, daemon=True).start()

    def on_unload(self):
        self._stop()
        super().on_unload()

    # ------------------------------------------------------------------
    # Daemon control
    # ------------------------------------------------------------------

    def _running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _port_in_use(self, port: int) -> bool:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("0.0.0.0", port))
            return False
        except OSError:
            return True
        finally:
            s.close()

    def _start(self):
        binp = _gmediarender_bin()
        if not binp:
            self._set_status("gmediarender not installed", error=True)
            return
        if self._running():
            self._stop()
            time.sleep(0.5)

        name = self.config.get("renderer_name", DEFAULT_NAME) or DEFAULT_NAME
        port = int(self.config.get("renderer_port", DEFAULT_PORT))
        if self._port_in_use(port):
            self._set_status(
                f"Port {port} is already in use - is another renderer running?",
                error=True)
            return

        cmd = [binp, "-f", name, "-p", str(port)]
        dev = self.config.get("renderer_alsa_device", "") or _alsa_device()
        if dev:
            # Send audio to the same DAC PulseBox uses
            cmd += ["--gstout-audiosink=alsasink",
                    f"--gstout-audiodevice={dev}"]
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            time.sleep(1.5)
            if self._running():
                self._set_status(f"Ready - cast to '{name}' from your phone")
            else:
                err = ""
                try:
                    err = (self._proc.stderr.read() or b"").decode(
                        "utf-8", "ignore").strip()
                except Exception:
                    pass
                detail = err.splitlines()[-1] if err else "unknown error"
                self._set_status(f"Failed to start: {detail}", error=True)
        except Exception as e:
            self._set_status(f"Failed to start: {e}", error=True)

    def _stop(self):
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None

    # ------------------------------------------------------------------

    def _set_status(self, msg: str, error: bool = False):
        print(f"[Renderer] {'ERROR' if error else 'OK'}: {msg}")
        if self._status_var and self.app:
            try:
                self.app.root.after(0, lambda: self._status_var.set(msg))
            except Exception:
                pass

    def _toggle_enabled(self):
        enabled = bool(self._enabled_var.get())
        if enabled and not _gmediarender_bin():
            self._set_status(
                "gmediarender not installed - run: sudo apt install gmediarender",
                error=True)
            self._enabled_var.set(False)
            self.config.set("renderer_enabled", False)
            return
        self.config.set("renderer_enabled", enabled)
        if enabled:
            self._set_status("Starting...")
            threading.Thread(target=self._start, daemon=True).start()
        else:
            self._stop()
            self._set_status("Stopped")

    def _apply(self):
        self.config.set("renderer_name",
                        (self._name_var.get().strip() or DEFAULT_NAME))
        if self.config.get("renderer_enabled", False):
            self._set_status("Restarting...")
            threading.Thread(target=self._start, daemon=True).start()
        else:
            self._set_status("Saved")

    # ------------------------------------------------------------------
    # UI panel
    # ------------------------------------------------------------------

    def get_ui_panel(self, parent) -> tk.Frame:
        from ui.theme import (BG_PANEL, BG_CARD, ACCENT, NEON_BLUE, DANGER,
                              TEXT_PRIMARY, TEXT_SECONDARY,
                              FONT_BODY, FONT_SMALL, FONT_TINY)

        frame = tk.Frame(parent, bg=BG_PANEL)
        installed = bool(_gmediarender_bin())

        top = tk.Frame(frame, bg=BG_PANEL)
        top.pack(fill="x", pady=(0, 6))
        self._enabled_var = tk.BooleanVar(
            value=self.config.get("renderer_enabled", False))
        tk.Checkbutton(top, text="Allow devices to cast to PulseBox",
                       variable=self._enabled_var, font=FONT_BODY,
                       fg=TEXT_PRIMARY, bg=BG_PANEL, activebackground=BG_PANEL,
                       selectcolor=BG_CARD, cursor="hand2",
                       command=self._toggle_enabled).pack(side="left")

        state = ("gmediarender not installed" if not installed
                 else ("Ready" if self._running() else "Stopped"))
        self._status_var = tk.StringVar(value=state)
        tk.Label(frame, textvariable=self._status_var, font=FONT_SMALL,
                 fg=NEON_BLUE, bg=BG_PANEL).pack(anchor="w", pady=(0, 8))

        if not installed:
            tk.Label(frame,
                     text="Install the renderer first:\n"
                          "    sudo apt install gmediarender",
                     font=FONT_TINY, fg=DANGER, bg=BG_PANEL,
                     justify="left").pack(anchor="w", pady=(0, 8))

        nrow = tk.Frame(frame, bg=BG_PANEL)
        nrow.pack(fill="x", pady=3)
        tk.Label(nrow, text="Cast name:", font=FONT_SMALL, fg=TEXT_PRIMARY,
                 bg=BG_PANEL, width=12, anchor="w").pack(side="left")
        self._name_var = tk.StringVar(
            value=self.config.get("renderer_name", DEFAULT_NAME))
        tk.Entry(nrow, textvariable=self._name_var, font=FONT_SMALL,
                 bg=BG_CARD, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
                 relief="flat", width=22).pack(side="left", padx=4)

        tk.Button(frame, text="Apply & Restart", font=FONT_SMALL, fg=BG_PANEL,
                  bg=ACCENT, relief="flat", bd=0, cursor="hand2",
                  padx=12, pady=5,
                  command=self._apply).pack(anchor="w", pady=(8, 0))

        tk.Label(frame,
                 text="Chromecast itself is proprietary — only Google-certified "
                      "devices can appear in Android's Cast menu. UPnP/DLNA is "
                      "the open equivalent: install BubbleUPnP, Hi-Fi Cast or "
                      "VLC on your phone and pick 'PulseBox' as the player. "
                      "Audio plays through your HiFiBerry.",
                 font=FONT_TINY, fg=TEXT_SECONDARY, bg=BG_PANEL,
                 wraplength=460, justify="left").pack(anchor="w", pady=(10, 0))

        return frame
