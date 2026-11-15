"""
PulseBox Module: DVD Player
===========================
Plays a video DVD from the optical drive.

WHY A SEPARATE VLC PROCESS?
    PulseBox's own player is an audio-only VLC instance. DVD *video* is played
    by launching a standalone fullscreen vlc.exe, which opens its own window
    over the app. Before launching, PulseBox's own playback is stopped. Press
    Stop to close the DVD and return to PulseBox.

NOTES / LIMITATIONS
    * Uses "dvdsimple://" by default — plays the main feature and skips the
      disc menus.
    * VLC for Windows ships with libdvdcss, so encrypted discs need nothing
      extra.

REQUIREMENTS
    VLC media player installed, and a DVD drive.
"""

import subprocess
import threading
import tkinter as tk

from core.config import JukeboxModule, APP_VERSION
from core import winplat


def _find_dvd_device() -> str | None:
    """First optical drive, e.g. 'D:'."""
    return winplat.first_optical_drive()


def _vlc_bin() -> str:
    """vlc.exe — on PATH or in Program Files\\VideoLAN\\VLC."""
    return winplat.find_exe("vlc")


class DVDPlayerModule(JukeboxModule):

    MODULE_ID          = "dvd_player"
    MODULE_NAME        = "DVD Player"
    MODULE_VERSION     = APP_VERSION
    MODULE_DESCRIPTION = ("Plays a video DVD on the attached screen via a "
                          "fullscreen VLC window. Stops PulseBox audio while "
                          "playing. Needs VLC media player installed.")
    MODULE_AUTHOR      = "PulseBox"

    def __init__(self):
        super().__init__()
        self._proc       = None
        self._device     = None
        self._status_var = None

    def on_load(self, app):
        super().on_load(app)
        self._device = _find_dvd_device()

    def _running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    def play(self):
        if self._running():
            self._set_status("DVD already playing — press Stop first")
            return
        binp = _vlc_bin()
        if not binp:
            self._set_status("VLC not found — install VLC media player",
                             error=True)
            return
        self._device = _find_dvd_device()
        if not self._device:
            self._set_status("No optical drive found", error=True)
            return

        # Stop PulseBox's own audio so the two don't fight over the DAC.
        try:
            self.app.player.stop()
        except Exception:
            pass

        # --intf dummy = no VLC control window, just the fullscreen video.
        # Audio goes to the Windows default playback device.
        cmd = [binp, "--fullscreen", "--no-video-title-show",
               "--play-and-exit", "--intf", "dummy", "--dummy-quiet"]
        # dvdsimple:// plays the main feature and skips menus. Swap to dvd://
        # if you want menus.
        cmd.append(f"dvdsimple:///{self._device[:2]}/")

        try:
            self._proc = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=winplat.NO_WINDOW)
            self._set_status("Playing DVD (fullscreen) — press Stop to return")
        except Exception as e:
            self._set_status(f"Failed to start VLC: {e}", error=True)

    def stop(self):
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
        self._set_status("Stopped")

    def eject(self):
        self.stop()
        if self._device:
            if winplat.eject(self._device):
                self._set_status("Ejected")
            else:
                self._set_status("Eject failed", error=True)

    def on_unload(self):
        self.stop()
        super().on_unload()

    # ------------------------------------------------------------------

    def _set_status(self, msg: str, error: bool = False):
        print(f"[DVD Player] {'ERROR' if error else 'OK'}: {msg}")
        if self._status_var and self.app:
            try:
                self.app.root.after(0, lambda: self._status_var.set(msg))
            except Exception:
                pass

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def get_ui_panel(self, parent) -> tk.Frame:
        from ui.theme import (BG_PANEL, BG_CARD, ACCENT, NEON_BLUE, DANGER,
                              TEXT_PRIMARY, TEXT_SECONDARY,
                              FONT_BODY, FONT_SMALL, FONT_TINY)

        frame = tk.Frame(parent, bg=BG_PANEL)

        self._status_var = tk.StringVar(
            value="Insert a DVD and press Play")
        tk.Label(frame, textvariable=self._status_var, font=FONT_BODY,
                 fg=NEON_BLUE, bg=BG_PANEL).pack(anchor="w", pady=(0, 8))

        row = tk.Frame(frame, bg=BG_PANEL)
        row.pack(anchor="w")

        def _btn(txt, cmd, fg=TEXT_PRIMARY, bg=BG_CARD):
            return tk.Button(row, text=txt, font=FONT_SMALL, fg=fg, bg=bg,
                             relief="flat", bd=0, cursor="hand2",
                             padx=14, pady=8,
                             command=lambda: threading.Thread(
                                 target=cmd, daemon=True).start())

        _btn("▶ Play DVD", self.play, fg=BG_PANEL, bg=ACCENT).pack(
            side="left", padx=(0, 6))
        _btn("⏹ Stop", self.stop).pack(side="left", padx=(0, 6))
        _btn("⏏ Eject", self.eject).pack(side="left", padx=(0, 6))

        tk.Label(frame,
                 text="DVD video opens in its own fullscreen window over "
                      "PulseBox (its audio pauses while the DVD plays). Plays "
                      "the main feature and skips disc menus.",
                 font=FONT_TINY, fg=TEXT_SECONDARY, bg=BG_PANEL,
                 wraplength=460, justify="left").pack(anchor="w", pady=(12, 0))

        if not _vlc_bin():
            tk.Label(frame, text="VLC not found — install VLC media player (videolan.org)",
                     font=FONT_TINY, fg=DANGER, bg=BG_PANEL).pack(
                anchor="w", pady=(8, 0))
        if not _find_dvd_device():
            tk.Label(frame, text="No optical drive detected.",
                     font=FONT_TINY, fg=DANGER, bg=BG_PANEL).pack(
                anchor="w", pady=(4, 0))

        return frame
