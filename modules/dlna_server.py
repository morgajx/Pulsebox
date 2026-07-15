"""
PulseBox Module: DLNA / UPnP Media Server
=========================================
Shares the PulseBox music and video library over DLNA/UPnP so other devices on
the network — smart TVs, other players, phone apps like BubbleUPnP — can browse
and play PulseBox's collection.

This wraps **minidlna** (ReadyMedia), a small, reliable DLNA server. PulseBox
runs it as a per-user daemon with its own config, so no root is needed at run
time. Install the server once with:

    sudo apt install minidlna

The PulseBox installer does this for you and disables the packaged system
service so this module can own the DLNA port.
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

WORK_DIR   = os.path.expanduser("~/.pulsebox_dlna")
CONF_PATH  = os.path.join(WORK_DIR, "minidlna.conf")
PID_PATH   = os.path.join(WORK_DIR, "minidlna.pid")
CACHE_DIR  = os.path.join(WORK_DIR, "cache")
LOG_DIR    = WORK_DIR

DEFAULT_NAME = "PulseBox"
DEFAULT_PORT = 8200


def _minidlna_bin() -> str:
    """Return the path to the minidlna daemon, or '' if not installed."""
    for name in ("minidlnad", "minidlna"):
        p = shutil.which(name)
        if p:
            return p
    return ""


class DLNAServerModule(JukeboxModule):

    MODULE_ID          = "dlna_server"
    MODULE_NAME        = "DLNA Media Server"
    MODULE_VERSION     = APP_VERSION
    MODULE_DESCRIPTION = (
        "Shares your music and video library over DLNA/UPnP so TVs and other "
        "devices can browse and play it. Requires the 'minidlna' package.")
    MODULE_AUTHOR      = "PulseBox"

    def __init__(self):
        super().__init__()
        self._status_var  = None
        self._name_var    = None
        self._music_var   = None
        self._video_var   = None
        self._enabled_var = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_load(self, app):
        super().on_load(app)
        # Auto-start the server if it was left enabled and minidlna is present.
        if self.config.get("dlna_enabled", False) and _minidlna_bin():
            threading.Thread(target=self._start, daemon=True).start()

    def on_unload(self):
        self._stop()
        super().on_unload()

    # ------------------------------------------------------------------
    # Daemon control
    # ------------------------------------------------------------------

    def _running_pid(self) -> int:
        """Return the PID of our minidlna instance if alive, else 0."""
        try:
            with open(PID_PATH) as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)          # raises if the process is gone
            return pid
        except Exception:
            return 0

    def _write_config(self) -> bool:
        """Write minidlna.conf from current settings. False if nothing to share."""
        music = self.config.get("music_dir", os.path.expanduser("~/Music"))
        video = self.config.get("dvd_output_dir", os.path.expanduser("~/Videos"))
        name  = self.config.get("dlna_name", DEFAULT_NAME) or DEFAULT_NAME
        port  = int(self.config.get("dlna_port", DEFAULT_PORT))
        share_music = self.config.get("dlna_share_music", True)
        share_video = self.config.get("dlna_share_video", True)

        os.makedirs(CACHE_DIR, exist_ok=True)
        os.makedirs(LOG_DIR, exist_ok=True)

        lines = [
            f"friendly_name={name}",
            f"db_dir={CACHE_DIR}",
            f"log_dir={LOG_DIR}",
            f"port={port}",
            "inotify=yes",
            "notify_interval=60",
            "album_art_names=Cover.jpg/cover.jpg/Folder.jpg/folder.jpg",
        ]
        any_dir = False
        if share_music and os.path.isdir(music):
            lines.append(f"media_dir=A,{music}")
            any_dir = True
        if share_video and os.path.isdir(video):
            lines.append(f"media_dir=V,{video}")
            any_dir = True
        if not any_dir:
            return False

        with open(CONF_PATH, "w") as f:
            f.write("\n".join(lines) + "\n")
        return True

    def _port_in_use(self, port: int) -> bool:
        """True if something is already listening on the DLNA TCP port."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("0.0.0.0", port))
            return False
        except OSError:
            return True
        finally:
            s.close()

    def _free_system_minidlna(self, port: int) -> bool:
        """
        Try to stop and disable the packaged system minidlna service so our
        per-user instance can own the port. Uses non-interactive sudo, so it
        succeeds silently where passwordless sudo is configured (the default
        on Raspberry Pi OS) and fails harmlessly otherwise. Returns True if
        the port ended up free.
        """
        for args in (["sudo", "-n", "systemctl", "disable", "--now", "minidlna"],
                     ["sudo", "-n", "systemctl", "stop", "minidlna"]):
            try:
                subprocess.run(args, timeout=10,
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
            except Exception:
                pass
        time.sleep(1.0)
        return not self._port_in_use(port)

    def _log_tail(self, lines: int = 4) -> str:
        """Return the last few lines of minidlna's log, if any."""
        try:
            log = os.path.join(LOG_DIR, "minidlna.log")
            with open(log, encoding="utf-8", errors="ignore") as f:
                return " | ".join(l.strip() for l in f.readlines()[-lines:]
                                  if l.strip())
        except Exception:
            return ""

    def _start(self, rescan: bool = False):
        binp = _minidlna_bin()
        if not binp:
            self._set_status("minidlna not installed", error=True)
            return
        if self._running_pid():
            self._stop()
            time.sleep(0.5)
        if not self._write_config():
            self._set_status("No media directories to share", error=True)
            return

        port = int(self.config.get("dlna_port", DEFAULT_PORT))
        name = self.config.get("dlna_name", DEFAULT_NAME)

        # If the port is already taken it's almost always the packaged system
        # minidlna service. Try to shut it down automatically (works when the
        # user has passwordless sudo, as on default Raspberry Pi OS). If that
        # isn't possible, fall back to a clear manual instruction.
        if self._port_in_use(port):
            self._set_status("Port in use - freeing system minidlna...")
            if not self._free_system_minidlna(port):
                self._set_status(
                    f"Port {port} is in use by the system minidlna and it "
                    f"couldn't be stopped automatically. Free it with: "
                    f"sudo systemctl disable --now minidlna", error=True)
                return

        cmd = [binp, "-f", CONF_PATH, "-P", PID_PATH]
        if rescan:
            cmd.append("-R")          # force a full library rescan on start
        try:
            # minidlnad daemonises (the parent exits), so this returns quickly.
            result = subprocess.run(cmd, timeout=30,
                                    capture_output=True, text=True)
        except Exception as e:
            self._set_status(f"Failed to start: {e}", error=True)
            return

        # The daemon forks and writes its pidfile a moment later — poll for it
        # rather than checking once and racing the fork.
        pid = 0
        for _ in range(25):          # up to ~5s
            pid = self._running_pid()
            if pid:
                break
            time.sleep(0.2)

        if pid:
            self._set_status(
                f"Running - discoverable as '{name}' (port {port})")
        else:
            # Surface the real reason from minidlna's own output / log
            err = (result.stderr or result.stdout or "").strip()
            detail = err.splitlines()[-1] if err else self._log_tail()
            self._set_status(
                f"minidlna did not start: {detail or 'unknown error'}",
                error=True)

    def _stop(self):
        pid = self._running_pid()
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:
                pass
        try:
            if os.path.exists(PID_PATH):
                os.remove(PID_PATH)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Status helper (marshals to the main thread)
    # ------------------------------------------------------------------

    def _set_status(self, msg: str, error: bool = False):
        print(f"[DLNA] {'ERROR' if error else 'OK'}: {msg}")
        if self._status_var and self.app:
            self.app.root.after(0, lambda: self._status_var.set(msg))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _toggle_enabled(self):
        enabled = bool(self._enabled_var.get())
        if enabled and not _minidlna_bin():
            self._set_status(
                "minidlna not installed - run: sudo apt install minidlna",
                error=True)
            self._enabled_var.set(False)
            self.config.set("dlna_enabled", False)
            return
        self.config.set("dlna_enabled", enabled)
        if enabled:
            self._set_status("Starting...")
            threading.Thread(target=self._start, daemon=True).start()
        else:
            self._stop()
            self._set_status("Stopped")

    def _apply_settings(self):
        self.config.set("dlna_name",
                        (self._name_var.get().strip() or DEFAULT_NAME))
        self.config.set("dlna_share_music", bool(self._music_var.get()))
        self.config.set("dlna_share_video", bool(self._video_var.get()))
        if self.config.get("dlna_enabled", False):
            self._set_status("Restarting...")
            threading.Thread(target=lambda: self._start(rescan=True),
                             daemon=True).start()
        else:
            self._set_status("Saved")

    def _rescan(self):
        if not self.config.get("dlna_enabled", False):
            self._set_status("Enable the server first")
            return
        self._set_status("Rescanning...")
        threading.Thread(target=lambda: self._start(rescan=True),
                         daemon=True).start()

    # ------------------------------------------------------------------
    # UI panel
    # ------------------------------------------------------------------

    def get_ui_panel(self, parent) -> tk.Frame:
        from ui.theme import (BG_PANEL, BG_CARD, ACCENT, NEON_BLUE,
                              DANGER, TEXT_PRIMARY, TEXT_SECONDARY,
                              FONT_BODY, FONT_SMALL, FONT_TINY)

        frame = tk.Frame(parent, bg=BG_PANEL)
        installed = bool(_minidlna_bin())

        # ── Enable toggle ─────────────────────────────────────────────
        top = tk.Frame(frame, bg=BG_PANEL)
        top.pack(fill="x", pady=(0, 6))
        self._enabled_var = tk.BooleanVar(
            value=self.config.get("dlna_enabled", False))
        tk.Checkbutton(top, text="Share library over DLNA/UPnP",
                       variable=self._enabled_var, font=FONT_BODY,
                       fg=TEXT_PRIMARY, bg=BG_PANEL, activebackground=BG_PANEL,
                       selectcolor=BG_CARD, cursor="hand2",
                       command=self._toggle_enabled).pack(side="left")

        # ── Status ────────────────────────────────────────────────────
        if not installed:
            init_status = "minidlna not installed"
        elif self._running_pid():
            init_status = "Running"
        else:
            init_status = "Stopped"
        self._status_var = tk.StringVar(value=init_status)
        tk.Label(frame, textvariable=self._status_var, font=FONT_SMALL,
                 fg=NEON_BLUE, bg=BG_PANEL).pack(anchor="w", pady=(0, 8))

        if not installed:
            tk.Label(frame,
                     text="Install the server first:\n"
                          "    sudo apt install minidlna",
                     font=FONT_TINY, fg=DANGER, bg=BG_PANEL,
                     justify="left").pack(anchor="w", pady=(0, 8))

        # ── Server name ───────────────────────────────────────────────
        nrow = tk.Frame(frame, bg=BG_PANEL)
        nrow.pack(fill="x", pady=3)
        tk.Label(nrow, text="Server name:", font=FONT_SMALL, fg=TEXT_PRIMARY,
                 bg=BG_PANEL, width=14, anchor="w").pack(side="left")
        self._name_var = tk.StringVar(
            value=self.config.get("dlna_name", DEFAULT_NAME))
        tk.Entry(nrow, textvariable=self._name_var, font=FONT_SMALL,
                 bg=BG_CARD, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
                 relief="flat", width=22).pack(side="left", padx=4)

        # ── Share toggles ─────────────────────────────────────────────
        self._music_var = tk.BooleanVar(
            value=self.config.get("dlna_share_music", True))
        self._video_var = tk.BooleanVar(
            value=self.config.get("dlna_share_video", True))
        for label, var in (("Share Music", self._music_var),
                           ("Share Video", self._video_var)):
            tk.Checkbutton(frame, text=label, variable=var, font=FONT_SMALL,
                           fg=TEXT_PRIMARY, bg=BG_PANEL,
                           activebackground=BG_PANEL, selectcolor=BG_CARD,
                           cursor="hand2").pack(anchor="w")

        # ── Directories being shared ──────────────────────────────────
        music = self.config.get("music_dir", "~/Music")
        video = self.config.get("dvd_output_dir", "~/Videos")
        tk.Label(frame, text=f"Music:  {music}\nVideo:  {video}",
                 font=FONT_TINY, fg=TEXT_SECONDARY, bg=BG_PANEL,
                 justify="left").pack(anchor="w", pady=(6, 8))

        # ── Buttons ───────────────────────────────────────────────────
        brow = tk.Frame(frame, bg=BG_PANEL)
        brow.pack(fill="x", pady=(4, 0))
        tk.Button(brow, text="Apply & Restart", font=FONT_SMALL, fg=BG_PANEL,
                  bg=ACCENT, relief="flat", bd=0, cursor="hand2",
                  padx=12, pady=5, command=self._apply_settings).pack(side="left")
        tk.Button(brow, text="Rescan Library", font=FONT_SMALL, fg=TEXT_PRIMARY,
                  bg=BG_CARD, relief="flat", bd=0, cursor="hand2",
                  padx=12, pady=5, command=self._rescan).pack(side="left", padx=6)

        # ── Hint ──────────────────────────────────────────────────────
        tk.Label(frame,
                 text="Once enabled, other DLNA devices on your network (smart "
                      "TVs, media players, apps like BubbleUPnP) will discover "
                      "your library automatically. The first scan of a large "
                      "library can take a few minutes.",
                 font=FONT_TINY, fg=TEXT_SECONDARY, bg=BG_PANEL,
                 wraplength=460, justify="left").pack(anchor="w", pady=(10, 0))

        return frame
