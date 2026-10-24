"""
PulseBox Module: Audio CD Player
================================
Plays an audio CD directly from the optical drive, through PulseBox's normal
audio pipeline — so the touch volume slider, the hardware mixer and the Stop
button all work exactly as they do for library tracks.

HOW IT WORKS
    Audio CDs aren't files, so they can't go through the normal library path.
    Each track is played via VLC's "cdda://" MRL using the player's play_mrl()
    helper (":cdda-track=N" selects the track). The module keeps its own small
    track list and advances automatically on the player's track_ended event,
    giving Play / Stop / Next / Previous.

    This is the counterpart to the CD *ripper* module: the ripper copies the
    disc to files; this one just plays it.

REQUIREMENTS
    cdparanoia (for disc detection + track count) and libvlc's cdda access,
    both already present on a standard PulseBox install. A CD/DVD drive.
"""

import os
import re
import subprocess
import threading
import time
import tkinter as tk

from core.config import JukeboxModule, APP_VERSION


# --- disc helpers (same approach as the CD ripper module) -------------------

def _find_cd_device() -> str | None:
    """First optical device that exists. Doesn't open it (that can block)."""
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
        candidates = ["/dev/cdrom", "/dev/sr0", "/dev/sr1", "/dev/dvd"]
    for dev in candidates:
        if os.path.exists(dev):
            return dev
    return None


def _audio_track_count(device: str) -> int:
    """
    Number of audio tracks on the disc via cdparanoia -Q, or 0 if none / no
    disc. Times out quickly so it never blocks the UI.
    """
    try:
        r = subprocess.run(["cdparanoia", "-Q", "-d", device],
                           capture_output=True, text=True, timeout=8)
        out = r.stdout + r.stderr
        # Each audio track lists as e.g. "  1.   ..." — count those lines.
        return len(re.findall(r"^\s+\d+\.\s", out, re.MULTILINE))
    except FileNotFoundError:
        print("[CD Player] cdparanoia not installed — cannot read the TOC.")
        return 0
    except Exception:
        return 0


class CDPlayerModule(JukeboxModule):

    MODULE_ID          = "cd_player"
    MODULE_NAME        = "CD Player"
    MODULE_VERSION     = APP_VERSION
    MODULE_DESCRIPTION = ("Plays an audio CD from the optical drive through "
                          "PulseBox's own audio output (volume slider, hardware "
                          "mixer and Stop all apply). Needs cdparanoia + a drive.")
    MODULE_AUTHOR      = "PulseBox"

    def __init__(self):
        super().__init__()
        self._device       = None
        self._track_count  = 0
        self._index        = 0        # 0-based current track
        self._active       = False    # are we the current audio source?
        self._status_var   = None
        self._track_var    = None

    def on_load(self, app):
        super().on_load(app)
        self._device = _find_cd_device()
        # Advance to the next CD track automatically when one ends — but only
        # while the CD is the active source, so we don't hijack library playback.
        try:
            self.app.player.on_event("track_ended", self._on_track_ended)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    def _mrl(self) -> str:
        return f"cdda://{self._device}"

    def _play_index(self, i: int):
        if not (0 <= i < self._track_count):
            return
        self._index = i
        n = i + 1
        ok = self.app.player.play_mrl(
            self._mrl(),
            options=[f":cdda-track={n}"],
            meta={"title": f"CD Track {n}",
                  "artist": "Audio CD",
                  "album": f"Track {n} of {self._track_count}"})
        if ok:
            self._active = True
            self._set_status(f"Playing track {n} of {self._track_count}")
            if self._track_var:
                self._track_var.set(f"Track {n} / {self._track_count}")

    def _scan_and_play(self):
        self._device = _find_cd_device()
        if not self._device:
            self._set_status("No optical drive found", error=True)
            return
        self._set_status("Reading disc…")
        count = _audio_track_count(self._device)
        if count <= 0:
            self._set_status("No audio CD detected in the drive", error=True)
            return
        self._track_count = count
        self._play_index(0)

    def play(self):
        threading.Thread(target=self._scan_and_play, daemon=True).start()

    def stop(self):
        self._active = False
        try:
            self.app.player.stop()
        except Exception:
            pass
        self._set_status("Stopped")

    def next_track(self):
        if self._active and self._index + 1 < self._track_count:
            self._play_index(self._index + 1)

    def prev_track(self):
        if self._active and self._index > 0:
            self._play_index(self._index - 1)

    def eject(self):
        self.stop()
        if self._device:
            try:
                subprocess.run(["eject", self._device], timeout=10)
                self._set_status("Ejected")
            except Exception as e:
                self._set_status(f"Eject failed: {e}", error=True)

    def _on_track_ended(self):
        # Auto-advance only while the CD owns playback.
        if not self._active:
            return
        if self._index + 1 < self._track_count:
            self._play_index(self._index + 1)
        else:
            self._active = False
            self._set_status("End of disc")

    # ------------------------------------------------------------------

    def _set_status(self, msg: str, error: bool = False):
        print(f"[CD Player] {'ERROR' if error else 'OK'}: {msg}")
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
            value="Insert an audio CD and press Play")
        tk.Label(frame, textvariable=self._status_var, font=FONT_BODY,
                 fg=NEON_BLUE, bg=BG_PANEL).pack(anchor="w", pady=(0, 6))

        self._track_var = tk.StringVar(value="")
        tk.Label(frame, textvariable=self._track_var, font=FONT_SMALL,
                 fg=TEXT_PRIMARY, bg=BG_PANEL).pack(anchor="w", pady=(0, 8))

        row = tk.Frame(frame, bg=BG_PANEL)
        row.pack(anchor="w")

        def _btn(txt, cmd, fg=TEXT_PRIMARY, bg=BG_CARD):
            return tk.Button(row, text=txt, font=FONT_SMALL, fg=fg, bg=bg,
                             relief="flat", bd=0, cursor="hand2",
                             padx=14, pady=8,
                             command=lambda: threading.Thread(
                                 target=cmd, daemon=True).start())

        _btn("⏮", self.prev_track).pack(side="left", padx=(0, 6))
        _btn("▶ Play", self.play, fg=BG_PANEL, bg=ACCENT).pack(side="left",
                                                               padx=(0, 6))
        _btn("⏹ Stop", self.stop).pack(side="left", padx=(0, 6))
        _btn("⏭", self.next_track).pack(side="left", padx=(0, 6))
        _btn("⏏ Eject", self.eject).pack(side="left", padx=(0, 6))

        tk.Label(frame,
                 text="Plays the CD through PulseBox's own output, so the "
                      "volume slider and Stop button work normally. Tracks "
                      "advance automatically.",
                 font=FONT_TINY, fg=TEXT_SECONDARY, bg=BG_PANEL,
                 wraplength=460, justify="left").pack(anchor="w", pady=(12, 0))

        if not _find_cd_device():
            tk.Label(frame, text="No optical drive detected.",
                     font=FONT_TINY, fg=DANGER, bg=BG_PANEL).pack(
                anchor="w", pady=(8, 0))

        return frame
