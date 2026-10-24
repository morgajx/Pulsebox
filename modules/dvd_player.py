"""
PulseBox Module: DVD Player
===========================
Plays a video DVD from the optical drive.

WHY A SEPARATE VLC PROCESS?
    PulseBox's own player runs VLC with "--no-xlib" for audio only — it has no
    video surface, and the Tkinter/Wayland kiosk window can't host VLC's video
    output. So DVD *video* is played by launching a standalone fullscreen VLC
    process (the same approach the visualiser uses), which opens its own window
    over the app. Audio is routed to the same DAC PulseBox uses.

    Before launching, PulseBox's own playback is stopped so the two don't fight
    over the sound card. Press Stop to close the DVD and return to PulseBox.

NOTES / LIMITATIONS (stated honestly)
    * Uses "dvdsimple://" by default — plays the main feature and skips the
      disc menus, since menu navigation needs a mouse/keyboard a kiosk lacks.
    * Encrypted commercial DVDs need libdvdcss installed (libdvd-pkg on Debian).
    * This plays video on the attached screen; it is not audio-through-PulseBox
      like the CD player, because a DVD is a video experience.

REQUIREMENTS
    The "vlc" package (provides cvlc) and a DVD drive. libdvdcss for encrypted
    discs.
"""

import os
import shutil
import subprocess
import threading
import tkinter as tk

from core.config import JukeboxModule, APP_VERSION

try:
    # Reuse PulseBox's DAC detection so DVD audio hits the HiFiBerry.
    from core.player import _detect_alsa_card
except Exception:
    def _detect_alsa_card():
        return ("", "")


def _find_dvd_device() -> str | None:
    candidates = []
    try:
        with open("/proc/sys/dev/cdrom/info") as f:
            for line in f:
                if line.startswith("drive name:"):
                    names = line.split(":")[1].strip().split()
                    candidates = [f"/dev/{n}" for n in names if n]
    except Exception:
        pass
    if not candidates:
        candidates = ["/dev/dvd", "/dev/sr0", "/dev/cdrom", "/dev/sr1"]
    for dev in candidates:
        if os.path.exists(dev):
            return dev
    return None


def _vlc_bin() -> str:
    for name in ("cvlc", "vlc"):
        p = shutil.which(name)
        if p:
            return p
    return ""


class DVDPlayerModule(JukeboxModule):

    MODULE_ID          = "dvd_player"
    MODULE_NAME        = "DVD Player"
    MODULE_VERSION     = APP_VERSION
    MODULE_DESCRIPTION = ("Plays a video DVD on the attached screen via a "
                          "fullscreen VLC window. Stops PulseBox audio while "
                          "playing. Needs the 'vlc' package (and libdvdcss for "
                          "encrypted discs).")
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
            self._set_status("VLC not installed (sudo apt install vlc)",
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

        cmd = [binp, "--fullscreen", "--no-video-title-show",
               "--play-and-exit", "--intf", "dummy"]
        # Route audio to the same DAC PulseBox uses, if we can detect it.
        dev, _card = _detect_alsa_card()
        if dev:
            cmd += ["--aout=alsa", f"--alsa-audio-device={dev}"]
        # dvdsimple:// plays the main feature and skips menus (no mouse on a
        # kiosk). Swap to dvd:// if you want menus.
        cmd.append(f"dvdsimple://{self._device}")

        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
            try:
                subprocess.run(["eject", self._device], timeout=10)
                self._set_status("Ejected")
            except Exception as e:
                self._set_status(f"Eject failed: {e}", error=True)

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
                      "the main feature and skips disc menus. Encrypted discs "
                      "need libdvdcss.",
                 font=FONT_TINY, fg=TEXT_SECONDARY, bg=BG_PANEL,
                 wraplength=460, justify="left").pack(anchor="w", pady=(12, 0))

        if not _vlc_bin():
            tk.Label(frame, text="VLC not installed — run: sudo apt install vlc",
                     font=FONT_TINY, fg=DANGER, bg=BG_PANEL).pack(
                anchor="w", pady=(8, 0))
        if not _find_dvd_device():
            tk.Label(frame, text="No optical drive detected.",
                     font=FONT_TINY, fg=DANGER, bg=BG_PANEL).pack(
                anchor="w", pady=(4, 0))

        return frame
