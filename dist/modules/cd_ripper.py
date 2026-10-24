"""
PulseBox Module: CD Ripper
Monitors for CD insertion and automatically rips using abcde.
Triggers a library rescan and cover art extraction when done.
"""

import os
import re
import subprocess
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from core.config import JukeboxModule, APP_VERSION

# ---------------------------------------------------------------------------
# System helpers
# ---------------------------------------------------------------------------

ABCDE_CONFIG_FLAC = (
    "# PulseBox abcde configuration - FLAC\n"
    "OUTPUTFORMAT='${ARTISTFILE}/${ALBUMFILE}/${TRACKNUM} - ${TRACKFILE}'\n"
    "OUTPUTTYPE=flac\n"
    "FLACENCODEROPT=\"--best\"\n"
    "EJECTCD=yes\n"
    "INTERACTIVE=n\n"
    "CDDBMETHOD=musicbrainz\n"
    "NOGAP=y\n"
    "MAXPROCS=$(nproc)\n"
    "# Preserve spaces in filenames — do not replace with underscores\n"
    "mungefilename ()\n"
    "{\n"
    "  echo \"$@\" | sed s,:,\\ -,g | tr -d '\"/\\\\'\n"
    "}\n"
)

ABCDE_CONFIG_M4A = (
    "# PulseBox abcde configuration - M4A (AAC via fdkaac)\n"
    "OUTPUTFORMAT='${ARTISTFILE}/${ALBUMFILE}/${TRACKNUM} - ${TRACKFILE}'\n"
    "OUTPUTTYPE=m4a\n"
    "AACENCODEROPT=\"-m 4 -a 256\"\n"
    "EJECTCD=yes\n"
    "INTERACTIVE=n\n"
    "CDDBMETHOD=musicbrainz\n"
    "NOGAP=y\n"
    "MAXPROCS=$(nproc)\n"
    "# Preserve spaces in filenames — do not replace with underscores\n"
    "mungefilename ()\n"
    "{\n"
    "  echo \"$@\" | sed s,:,\\ -,g | tr -d '\"/\\\\'\n"
    "}\n"
)

ABCDE_CONFIG_MP3 = (
    "# PulseBox abcde configuration - MP3 (lame)\n"
    "OUTPUTFORMAT='${ARTISTFILE}/${ALBUMFILE}/${TRACKNUM} - ${TRACKFILE}'\n"
    "OUTPUTTYPE=mp3\n"
    "LAMEOPTS=\"-V 0 --preset insane\"\n"
    "EJECTCD=yes\n"
    "INTERACTIVE=n\n"
    "CDDBMETHOD=musicbrainz\n"
    "NOGAP=y\n"
    "MAXPROCS=$(nproc)\n"
    "mungefilename ()\n"
    "{\n"
    "  echo \"$@\" | sed s,:,\\ -,g | tr -d '\"/\\\'\n"
    "}\n"
)

# Default written to disk if ~/.abcde.conf does not exist
ABCDE_CONFIG = ABCDE_CONFIG_FLAC

ABCDE_CONFIG_PATH = os.path.expanduser("~/.abcde.conf")


def _ensure_abcde_config():
    """Write a sensible abcde config if none exists."""
    if not os.path.exists(ABCDE_CONFIG_PATH):
        try:
            with open(ABCDE_CONFIG_PATH, "w") as f:
                f.write(ABCDE_CONFIG)
            print(f"[CDRipper] Wrote default abcde config to {ABCDE_CONFIG_PATH}")
        except Exception as e:
            print(f"[CDRipper] Could not write abcde config: {e}")


def _is_abcde_installed() -> bool:
    try:
        subprocess.run(["abcde", "--version"],
                       capture_output=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _find_cd_device() -> str | None:
    """
    Return the first CD-ROM device that exists on this system.
    Does NOT try to open or query the device — that can block
    if no disc is inserted. Actual disc detection is done by
    _is_audio_cd() using cdparanoia which handles this safely.
    """
    candidates = []

    # Prefer /proc/sys/dev/cdrom/info which lists real drive names
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


def _is_audio_cd(device: str) -> bool:
    """
    Use cdparanoia -Q to check for audio tracks.
    Returns True if the disc has at least one audio track.
    Times out quickly so it never blocks the monitor loop.
    """
    try:
        r = subprocess.run(
            ["cdparanoia", "-Q", "-d", device],
            capture_output=True, text=True, timeout=6)
        output = r.stdout + r.stderr
        return bool(re.search(r"^\s+\d+\.", output, re.MULTILINE))
    except FileNotFoundError:
        # cdparanoia not installed — use dd to test readability instead
        try:
            r = subprocess.run(
                ["dd", "if=" + device, "of=/dev/null",
                 "bs=2048", "count=1"],
                capture_output=True, timeout=4)
            return r.returncode == 0
        except Exception:
            return False
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return False


def _get_cd_info(device: str) -> dict:
    """Try to get disc info via cd-discid or cdparanoia."""
    info = {"tracks": 0, "discid": ""}
    try:
        r = subprocess.run(["cd-discid", device],
                           capture_output=True, text=True, timeout=8)
        if r.returncode == 0:
            parts = r.stdout.strip().split()
            info["discid"] = parts[0] if parts else ""
            info["tracks"] = int(parts[1]) if len(parts) > 1 else 0
    except Exception:
        pass
    return info


# ---------------------------------------------------------------------------
# Rip worker
# ---------------------------------------------------------------------------

def rip_cd(device: str, output_dir: str, output_format: str = "flac",
           log_cb=None, done_cb=None, error_cb=None):
    """
    Run abcde in a subprocess. Streams output to log_cb(line).
    output_format: "flac" or "m4a"
    Calls done_cb() on success or error_cb(msg) on failure.

    Working command (tested on Pi OS Trixie):
        abcde -d /dev/sr0 -N -x -c ~/.abcde.conf
    Output directory is set via OUTPUTDIR in the abcde config.
    """
    _ensure_abcde_config()

    # Write format-specific config
    fmt = output_format.lower().strip()
    if fmt == "m4a":
        cfg_content = ABCDE_CONFIG_M4A
    elif fmt == "mp3":
        cfg_content = ABCDE_CONFIG_MP3
    else:
        fmt = "flac"
        cfg_content = ABCDE_CONFIG_FLAC

    # Add OUTPUTDIR to the config so files go to the music library
    cfg_with_dir = cfg_content.rstrip() + f"\nOUTPUTDIR=\"{output_dir}\"\n"
    try:
        with open(ABCDE_CONFIG_PATH, "w") as f:
            f.write(cfg_with_dir)
    except Exception as e:
        if error_cb:
            error_cb(f"Could not write abcde config: {e}")
        return

    cmd = [
        "abcde",
        "-d", device,
        "-N",              # non-interactive
        "-x",              # eject when done
        "-c", ABCDE_CONFIG_PATH,
    ]

    if log_cb:
        log_cb(f"Format: {fmt.upper()}\n")
        log_cb(f"Output: {output_dir}\n")
        log_cb(f"$ {' '.join(cmd)}\n\n")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        for line in proc.stdout:
            if log_cb:
                log_cb(line)
        proc.wait()
        if proc.returncode == 0:
            if done_cb:
                done_cb()
        else:
            if error_cb:
                error_cb(f"abcde exited with code {proc.returncode}")
    except FileNotFoundError:
        if error_cb:
            error_cb("abcde not found — install: sudo apt install abcde")
    except Exception as e:
        if error_cb:
            error_cb(str(e))


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------

class CDRipper(JukeboxModule):
    MODULE_ID          = "cd_ripper"
    MODULE_NAME        = "CD Ripper"
    MODULE_VERSION = APP_VERSION
    MODULE_DESCRIPTION = ("Monitors for CD insertion and rips automatically "
                          "using abcde → FLAC. Triggers library rescan when done.")
    MODULE_AUTHOR      = "Andrew J. Morgan"

    # Poll interval for CD detection (seconds)
    POLL_INTERVAL = 8

    def __init__(self):
        super().__init__()
        self._monitor_thread: threading.Thread | None = None
        self._rip_thread:     threading.Thread | None = None
        self._stop_monitor    = threading.Event()
        self._ripping         = False
        self._last_device: str | None = None

        # UI vars (set when panel is built)
        self._status_var:   tk.StringVar | None = None
        self._log_text:     tk.Text | None = None
        self._rip_btn:      tk.Button | None = None
        self._device_var:   tk.StringVar | None = None
        self._auto_var:     tk.BooleanVar | None = None
        self._format_var:   tk.StringVar | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_load(self, app):
        super().on_load(app)
        _ensure_abcde_config()
        self._start_monitor()
        print("[CDRipper] Loaded — monitoring for CD insertion")

    def on_unload(self):
        self._stop_monitor.set()
        super().on_unload()

    # ------------------------------------------------------------------
    # CD monitor loop
    # ------------------------------------------------------------------

    def _start_monitor(self):
        self._stop_monitor.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()

    def _monitor_loop(self):
        disc_was_present = False
        device = _find_cd_device()   # find drive once at startup

        if not device:
            print("[CDRipper] No CD drive found — monitor idle")
            return

        print(f"[CDRipper] Monitoring drive: {device}")
        self._last_device = device
        if self._device_var:
            self.app.root.after(0, lambda: self._device_var.set(device))

        while not self._stop_monitor.is_set():
            # Use cdparanoia to check for disc — non-blocking and reliable
            disc_present = _is_audio_cd(device)

            if disc_present and not disc_was_present:
                self._last_device = device
                self._on_disc_inserted(device)
            elif not disc_present and disc_was_present:
                self._on_disc_removed()

            disc_was_present = disc_present
            self._stop_monitor.wait(timeout=self.POLL_INTERVAL)

    def _on_disc_inserted(self, device: str):
        print(f"[CDRipper] Disc detected: {device}")
        self._set_status(f"Disc detected: {device}")

        if self._auto_var and self._auto_var.get() and not self._ripping:
            # Small delay so disc spins up
            time.sleep(3)
            if _is_audio_cd(device):
                self._set_status(f"Audio CD detected — starting auto-rip")
                self.emit_notification("Audio CD detected — ripping started", "info")
                self._start_rip(device)
            else:
                self._set_status("Data disc detected — not ripping")
        else:
            if _is_audio_cd(device):
                self.emit_notification(
                    "Audio CD inserted. Go to Modules → CD Ripper to rip it.", "info")
                self._set_status("Audio CD ready. Press 'Rip CD' to start.")
            else:
                self._set_status("Data disc detected")

        if self._device_var:
            self.app.root.after(0, lambda: self._device_var.set(device))

    def _on_disc_removed(self):
        print("[CDRipper] Disc removed")
        if not self._ripping:
            self._set_status("No disc")
        if self._device_var:
            self.app.root.after(0, lambda: self._device_var.set("No disc"))

    # ------------------------------------------------------------------
    # Ripping
    # ------------------------------------------------------------------

    def _start_rip(self, device: str | None = None):
        if self._ripping:
            return
        if device is None:
            device = _find_cd_device()
        if not device:
            self._set_status("No disc found")
            self.emit_notification("No CD detected", "warning")
            return

        self._ripping = True
        self._set_status("Ripping…")
        if self._rip_btn:
            self.app.root.after(0, lambda: self._rip_btn.config(
                state="disabled", text="Ripping…"))

        output_dir = getattr(self, '_outdir_var', None)
        output_dir = output_dir.get() if output_dir else self.config.get("music_dir")
        output_dir = os.path.expanduser(output_dir or "~/Music")

        def _log(line):
            if self._log_text:
                self.app.root.after(0, lambda l=line: self._append_log(l))

        def _done():
            self._ripping = False
            self._set_status("Rip complete! Rescanning library…")
            self.emit_notification("CD ripped successfully! Rescanning library.", "success")
            if self._rip_btn:
                self.app.root.after(0, lambda: self._rip_btn.config(
                    state="normal", text="Rip CD"))
            # Trigger cover art extraction then library rescan
            self._post_rip_tasks()

        def _error(msg):
            self._ripping = False
            self._set_status(f"Error: {msg}")
            self.emit_notification(f"Rip failed: {msg}", "error")
            if self._rip_btn:
                self.app.root.after(0, lambda: self._rip_btn.config(
                    state="normal", text="Rip CD"))

        fmt = self._format_var.get() if self._format_var else "flac"
        self._rip_thread = threading.Thread(
            target=rip_cd,
            args=(device, output_dir, fmt),
            kwargs={"log_cb": _log, "done_cb": _done, "error_cb": _error},
            daemon=True)
        self._rip_thread.start()

    def _post_rip_tasks(self):
        """After a successful rip: extract cover art then rescan library."""
        def _run():
            # 1. Extract cover art for newly ripped album
            try:
                from modules.cover_art_extractor import scan_and_extract
                music_dir = self.config.get("music_dir")
                stats = scan_and_extract(music_dir, force=False)
                print(f"[CDRipper] Cover art: {stats}")
            except Exception as e:
                print(f"[CDRipper] Cover art extraction error: {e}")

            # 2. Rescan library
            if self.app and hasattr(self.app, "_rescan_library"):
                self.app.root.after(0, self.app._rescan_library)

        threading.Thread(target=_run, daemon=True).start()

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------

    def _set_status(self, msg: str):
        if self._status_var and self.app:
            self.app.root.after(0, lambda m=msg: self._status_var.set(m))

    def _append_log(self, line: str):
        if self._log_text:
            self._log_text.config(state="normal")
            self._log_text.insert("end", line)
            self._log_text.see("end")
            self._log_text.config(state="disabled")

    # ------------------------------------------------------------------
    # UI Panel
    # ------------------------------------------------------------------

    def _scan_drives(self) -> list:
        """Return list of available CD/DVD device paths."""
        import glob
        drives = []
        for pattern in ["/dev/sr*", "/dev/cdrom", "/dev/dvd"]:
            found = glob.glob(pattern)
            for d in found:
                if os.path.exists(d) and d not in drives:
                    drives.append(d)
        return drives if drives else ["/dev/sr0"]

    def _refresh_drives(self):
        """Rescan for drives and update dropdown."""
        drives = self._scan_drives()
        if hasattr(self, '_device_combo'):
            self._device_combo["values"] = drives
            if drives:
                current = self._device_var.get()
                if current not in drives:
                    self._device_var.set(drives[0])
        if hasattr(self, '_status_var') and self._status_var:
            self._status_var.set(
                f"Found: {', '.join(drives)}")

    def _browse_output_dir(self):
        """Open folder browser for output directory."""
        from tkinter import filedialog
        current = os.path.expanduser(
            getattr(self, '_outdir_var', None) and
            self._outdir_var.get() or
            self.config.get("music_dir", "~/Music"))
        chosen = filedialog.askdirectory(
            title="Select Output Directory",
            initialdir=current if os.path.isdir(current) else os.path.expanduser("~"))
        if chosen:
            self._outdir_var.set(chosen)
            self.config.set("music_dir", chosen)

    def get_ui_panel(self, parent) -> tk.Frame:
        from ui.theme import (BG_PANEL, BG_CARD, BG_DARK, ACCENT, NEON_BLUE,
                              SUCCESS, DANGER, WARNING, TEXT_PRIMARY,
                              TEXT_SECONDARY, TEXT_DIM,
                              FONT_BODY, FONT_SMALL, FONT_TINY, FONT_SUBHEAD)

        frame = tk.Frame(parent, bg=BG_PANEL)

        # ── abcde availability warning ──────────────────────────────
        if not _is_abcde_installed():
            warn = tk.Frame(frame, bg="#2a1500", padx=8, pady=6)
            warn.pack(fill="x", pady=(0, 8))
            tk.Label(warn,
                     text="⚠  abcde not installed.",
                     font=FONT_SMALL, fg=WARNING, bg="#2a1500").pack(anchor="w")
            tk.Label(warn,
                     text="Install with:  sudo apt install abcde flac cdparanoia",
                     font=FONT_TINY, fg=TEXT_PRIMARY, bg="#2a1500").pack(anchor="w")

        # ── Status row ───────────────────────────────────────────────
        self._status_var = tk.StringVar(value="Monitoring for disc…")
        self._device_var = tk.StringVar(value=_find_cd_device() or "No disc")

        row_status = tk.Frame(frame, bg=BG_PANEL)
        row_status.pack(fill="x", pady=2)
        tk.Label(row_status, text="Status:", font=FONT_SMALL,
                 fg=TEXT_PRIMARY, bg=BG_PANEL, width=10, anchor="w").pack(side="left")
        tk.Label(row_status, textvariable=self._status_var,
                 font=FONT_SMALL, fg=ACCENT, bg=BG_PANEL).pack(side="left")

        row_dev = tk.Frame(frame, bg=BG_PANEL)
        row_dev.pack(fill="x", pady=2)
        tk.Label(row_dev, text="Drive:", font=FONT_SMALL,
                 fg=TEXT_PRIMARY, bg=BG_PANEL, width=10, anchor="w").pack(side="left")

        # Dropdown of available CD devices
        self._device_combo = ttk.Combobox(
            row_dev, textvariable=self._device_var,
            font=FONT_SMALL, width=16, state="normal")
        self._device_combo["values"] = self._scan_drives()
        self._device_combo.pack(side="left", padx=(0, 6), ipady=2)

        tk.Button(row_dev, text="⟳ Scan",
                  font=FONT_TINY, fg=ACCENT, bg=BG_CARD,
                  relief="flat", bd=0, cursor="hand2", padx=6, pady=2,
                  command=self._refresh_drives).pack(side="left")

        # ── Auto-rip + Format — single aligned row ──────────────────
        self._auto_var = tk.BooleanVar(
            value=bool(self.config.get("cd_auto_rip", False)))
        self._format_var = tk.StringVar(
            value=self.config.get("cd_rip_format", "flac"))

        options_row = tk.Frame(frame, bg=BG_PANEL)
        options_row.pack(fill="x", pady=4)

        # Auto-rip checkbox aligned left with same width as "Drive:" label
        tk.Label(options_row, text="Options:", font=FONT_SMALL,
                 fg=TEXT_PRIMARY, bg=BG_PANEL,
                 width=10, anchor="w").pack(side="left")

        tk.Checkbutton(options_row,
                       text="Auto-rip on insertion",
                       variable=self._auto_var,
                       font=FONT_SMALL, fg=TEXT_PRIMARY,
                       bg=BG_PANEL, activebackground=BG_PANEL,
                       selectcolor=BG_CARD, cursor="hand2",
                       command=lambda: self.config.set(
                           "cd_auto_rip", self._auto_var.get())
                       ).pack(side="left", padx=(0, 16))

        # Format radio buttons on same row
        tk.Label(options_row, text="Format:", font=FONT_SMALL,
                 fg=TEXT_PRIMARY, bg=BG_PANEL).pack(side="left")

        for fmt, label in [("flac", "FLAC"),
                            ("m4a",  "M4A"),
                            ("mp3",  "MP3")]:
            tk.Radiobutton(
                options_row, text=label,
                variable=self._format_var, value=fmt,
                font=FONT_SMALL, fg=TEXT_PRIMARY,
                bg=BG_PANEL, activebackground=BG_PANEL,
                selectcolor=BG_CARD, cursor="hand2",
                command=lambda: self.config.set(
                    "cd_rip_format", self._format_var.get())
            ).pack(side="left", padx=6)

        # ── Output directory ─────────────────────────────────────────
        out_row = tk.Frame(frame, bg=BG_PANEL)
        out_row.pack(fill="x", pady=(0, 4))
        tk.Label(out_row, text="Output dir:", font=FONT_SMALL,
                 fg=TEXT_PRIMARY, bg=BG_PANEL,
                 width=10, anchor="w").pack(side="left")
        self._outdir_var = tk.StringVar(
            value=self.config.get("music_dir", "~/Music"))
        tk.Entry(out_row, textvariable=self._outdir_var,
                 font=FONT_SMALL, bg=BG_CARD, fg=TEXT_PRIMARY,
                 insertbackground=ACCENT, relief="flat",
                 width=28).pack(side="left", padx=(0, 6), ipady=3)
        tk.Button(out_row, text="Browse…",
                  font=FONT_TINY, fg=ACCENT, bg=BG_CARD,
                  relief="flat", bd=0, cursor="hand2", padx=6, pady=2,
                  command=self._browse_output_dir).pack(side="left")

        tk.Label(frame,
                 text=f"Config: {ABCDE_CONFIG_PATH}",
                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_PANEL).pack(
            anchor="w", pady=(0, 6))

        # fdkaac hint
        tk.Label(frame,
                 text="M4A requires: sudo apt install fdkaac abcde",
                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_PANEL).pack(
            anchor="w", pady=(0, 4))

        # ── Rip button ───────────────────────────────────────────────
        btn_row = tk.Frame(frame, bg=BG_PANEL)
        btn_row.pack(fill="x", pady=(0, 6))
        self._rip_btn = tk.Button(
            btn_row, text="Rip CD",
            font=FONT_SMALL, fg=BG_PANEL, bg=ACCENT,
            relief="flat", bd=0, cursor="hand2", padx=14, pady=6,
            command=lambda: self._start_rip())
        self._rip_btn.pack(side="left", padx=(0, 8))

        tk.Button(btn_row, text="Eject",
                  font=FONT_SMALL, fg=TEXT_PRIMARY, bg=BG_CARD,
                  relief="flat", bd=0, cursor="hand2", padx=10, pady=6,
                  command=self._eject).pack(side="left")

        # ── Log output ───────────────────────────────────────────────
        tk.Label(frame, text="Rip log:", font=FONT_TINY,
                 fg=TEXT_PRIMARY, bg=BG_PANEL).pack(anchor="w")

        log_frame = tk.Frame(frame, bg=BG_DARK)
        log_frame.pack(fill="x")

        self._log_text = tk.Text(
            log_frame, height=8,
            font=("Courier", 8),
            bg="#0a0a0a", fg="#88ff88",
            insertbackground=ACCENT,
            state="disabled", relief="flat",
            wrap="word")
        self._log_text.pack(side="left", fill="x", expand=True)

        sb = tk.Scrollbar(log_frame, command=self._log_text.yview,
                          bg=BG_DARK, troughcolor=BG_DARK)
        sb.pack(side="right", fill="y")
        self._log_text.config(yscrollcommand=sb.set)

        tk.Button(frame, text="Clear log",
                  font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_PANEL,
                  relief="flat", bd=0, cursor="hand2",
                  command=self._clear_log).pack(anchor="e", pady=(2, 0))

        return frame

    def _eject(self):
        device = self._last_device or "/dev/cdrom"
        try:
            subprocess.run(["eject", device], timeout=5)
            self._set_status("Disc ejected")
        except Exception as e:
            self._set_status(f"Eject failed: {e}")

    def _clear_log(self):
        if self._log_text:
            self._log_text.config(state="normal")
            self._log_text.delete("1.0", "end")
            self._log_text.config(state="disabled")
