"""
PulseBox Module: DVD Ripper
============================
Rips a DVD to MP4 using MakeMKV (for decryption) and HandBrakeCLI
(for transcoding). Copy protection is bypassed via libdvdcss2 which
MakeMKV uses automatically when present.

Cover art is extracted from the DVD's VIDEO_TS/VIDEO_TS.IFO thumb if
present, otherwise fetched from The Movie Database (TMDb) API.
The poster is always saved as  <output_dir>/<title>/poster.jpg

Output structure
────────────────
  <video_output_dir>/
  └── <Movie Title> (Year)/
      ├── <Movie Title> (Year).mp4
      └── poster.jpg

Requirements (install once on the Pi)
──────────────────────────────────────
  # MakeMKV CLI (free beta, key at makemkv.com/forum/viewtopic.php?t=1053)
  sudo apt install makemkv-bin   # or build from source

  # HandBrakeCLI
  sudo apt install handbrake-cli

  # libdvdcss2 — copy protection bypass
  sudo apt install libdvd-pkg
  sudo dpkg-reconfigure libdvd-pkg

  # Optional: ffmpeg for thumb extraction
  sudo apt install ffmpeg

TMDb API key
────────────
  Free at https://www.themoviedb.org/settings/api
  Enter in the module UI or set config key  "tmdb_api_key".
"""

import glob
import io
import json
import os
import re
import shutil
import subprocess
import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk
from urllib.request import urlopen, Request
from urllib.error import URLError
from urllib.parse import quote_plus

from core.config import JukeboxModule, APP_VERSION
from ui.theme import (ACCENT, ACCENT2, BG_CARD, BG_DARK, BG_PANEL,
                      BORDER, DANGER, FONT_BODY, FONT_SMALL,
                      FONT_SUBHEAD, FONT_TINY, NEON_BLUE, SUCCESS,
                      TEXT_DIM, TEXT_PRIMARY, TEXT_SECONDARY, WARNING)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TMDB_SEARCH  = "https://api.themoviedb.org/3/search/movie"
TMDB_IMG     = "https://image.tmdb.org/t/p/w500"
MAKEMKV_BIN  = "makemkvcon"
HB_BIN       = "HandBrakeCLI"
DEFAULT_DISC = "/dev/sr0"

# HandBrakeCLI preset — H.264 RF 20, stereo AAC, fast encode
HB_PRESET = (
    "--encoder x264 --quality 20 --encoder-preset fast "
    "--aencoder av_aac --ab 160 --mixdown stereo "
    "--format av_mp4 --optimize"
)


# ---------------------------------------------------------------------------
# Tool availability checks
# ---------------------------------------------------------------------------

def _which(name: str) -> bool:
    try:
        subprocess.run(["which", name], capture_output=True, check=True, timeout=3)
        return True
    except Exception:
        return False


def _find_dvd_device() -> str | None:
    try:
        with open("/proc/sys/dev/cdrom/info") as f:
            for line in f:
                if line.startswith("drive name:"):
                    names = line.split(":")[1].strip().split()
                    for n in names:
                        dev = f"/dev/{n}"
                        if os.path.exists(dev):
                            return dev
    except Exception:
        pass
    for d in ["/dev/sr0", "/dev/dvd", "/dev/cdrom"]:
        if os.path.exists(d):
            return d
    return None


def _scan_drives() -> list[str]:
    drives = []
    for pat in ["/dev/sr*", "/dev/dvd", "/dev/cdrom"]:
        for d in glob.glob(pat):
            if os.path.exists(d) and d not in drives:
                drives.append(d)
    return drives or ["/dev/sr0"]


def _disc_title_from_makemkv(device: str) -> str:
    """Ask makemkvcon for the disc title (fast info query).
    Uses disc:0 (MakeMKV's drive index) for consistency with the rip
    command — disc:/dev/srN can trigger SCSI errors on some drives."""
    try:
        r = subprocess.run(
            [MAKEMKV_BIN, "-r", "info", "disc:0"],
            capture_output=True, text=True, timeout=30)
        for line in r.stdout.splitlines():
            # CINFO:2,0,"<title>"
            m = re.match(r'CINFO:2,0,"(.+)"', line)
            if m:
                return m.group(1).strip()
    except Exception:
        pass
    return ""


def _sanitise_filename(name: str) -> str:
    """Remove characters that are illegal in filenames."""
    return re.sub(r'[<>:"/\\|?*]', "", name).strip()


# ---------------------------------------------------------------------------
# Cover art helpers
# ---------------------------------------------------------------------------

def _extract_dvd_thumb(device: str) -> bytes | None:
    """
    Try to grab a thumbnail from the DVD using ffmpeg.
    Returns JPEG bytes or None.
    """
    if not _which("ffmpeg"):
        return None
    try:
        r = subprocess.run(
            ["ffmpeg", "-i", f"dvd://{device}", "-ss", "00:05:00",
             "-vframes", "1", "-q:v", "2", "-f", "image2", "pipe:1"],
            capture_output=True, timeout=30)
        if r.returncode == 0 and r.stdout:
            return r.stdout
    except Exception:
        pass
    return None


def _fetch_tmdb_poster(title: str, api_key: str,
                       year: str = "") -> bytes | None:
    """
    Search TMDb for `title`, return the poster as JPEG bytes or None.

    TMDb's search endpoint does its own fuzzy/relevance matching, so the
    top result is NOT necessarily the title we asked for — it can return
    a more popular film with a similar-sounding name. To reduce wrong-
    poster matches:
      1. Prefer a result whose title matches `title` exactly
         (case-insensitive, ignoring punctuation/whitespace).
      2. Otherwise, only fall back to the top result if its title is at
         least a reasonably close match (normalised substring match).
      3. If nothing matches closely enough, return None rather than a
         confidently-wrong poster.
    """
    if not api_key:
        return None
    try:
        query = quote_plus(title)
        url   = f"{TMDB_SEARCH}?api_key={api_key}&query={query}"
        if year:
            url += f"&year={year}"
        req  = Request(url, headers={"User-Agent": "PulseBox/1.0"})
        data = json.loads(urlopen(req, timeout=10).read())
        results = data.get("results", [])
        if not results:
            return None

        def _norm(s: str) -> str:
            return re.sub(r'[^a-z0-9]+', '', s.lower())

        wanted = _norm(title)

        best = None
        # 1. Exact normalised match (case/punctuation-insensitive)
        for r in results:
            cand = _norm(r.get("title", "") or r.get("original_title", ""))
            if cand == wanted:
                best = r
                break

        # 2. Close match — wanted title is a substring of result title or
        #    vice versa (handles "Jaws" vs "Jaws (1975)"-style differences,
        #    and disc titles with extra words stripped during sanitising).
        if best is None:
            for r in results:
                cand = _norm(r.get("title", "") or r.get("original_title", ""))
                if cand and (wanted in cand or cand in wanted):
                    best = r
                    break

        if best is None:
            print(f"[DVDRipper] TMDb: no close title match for '{title}' "
                  f"(top result was '{results[0].get('title')}') — "
                  f"skipping poster to avoid wrong artwork")
            return None

        poster_path = best.get("poster_path", "")
        if not poster_path:
            return None
        img_url = TMDB_IMG + poster_path
        img_req = Request(img_url, headers={"User-Agent": "PulseBox/1.0"})
        return urlopen(img_req, timeout=15).read()
    except Exception as e:
        print(f"[DVDRipper] TMDb fetch failed: {e}")
        return None


def _save_poster(folder: str, data: bytes) -> str:
    """Write poster bytes to <folder>/poster.jpg. Returns path."""
    path = os.path.join(folder, "poster.jpg")
    with open(path, "wb") as f:
        f.write(data)
    return path


# ---------------------------------------------------------------------------
# Rip pipeline  (makemkvcon → HandBrakeCLI)
# ---------------------------------------------------------------------------

def rip_dvd(device: str,
            output_dir: str,
            title_override: str = "",
            tmdb_api_key: str = "",
            log_cb=None,
            progress_cb=None,
            done_cb=None,
            error_cb=None):
    """
    Full pipeline:
      1. makemkvcon rips disc → MKV in a temp dir
      2. HandBrakeCLI transcodes MKV → MP4
      3. Cover art extracted or fetched, saved as poster.jpg
      4. Temp MKV deleted

    progress_cb(percent: int, stage: str)   — 0-100
    log_cb(line: str)
    done_cb(movie_dir: str)
    error_cb(msg: str)
    """

    def _log(msg):
        if log_cb:
            log_cb(msg + "\n")

    def _progress(pct, stage):
        if progress_cb:
            progress_cb(pct, stage)

    # ── Stage 1 : get disc title ──────────────────────────────────────
    _log("── Stage 1: Reading disc info…")
    _progress(2, "Reading disc…")
    disc_title = title_override or _disc_title_from_makemkv(device) or "DVD_TITLE"
    disc_title = _sanitise_filename(disc_title) or "DVD_TITLE"
    _log(f"   Title: {disc_title}")

    # Temp dir for MKV output
    import tempfile
    # NOTE: /tmp is often tmpfs (RAM-backed) on Raspberry Pi OS, sized as a
    # fraction of total RAM (e.g. ~3.8GB on an 8GB Pi 5). A dual-layer DVD
    # rip needs 7-8GB, which won't fit in tmpfs even though the SD card has
    # plenty of space. Use a directory on the SD card instead.
    tmp_base = os.path.expanduser("~/.pulsebox_dvd_tmp")
    os.makedirs(tmp_base, exist_ok=True)
    tmp_dir = tempfile.mkdtemp(prefix="rip_", dir=tmp_base)
    _log(f"   Temp dir: {tmp_dir}")

    # Warn early if free space looks tight — a dual-layer DVD can need
    # 7-8GB. MakeMKV will still prompt mid-rip if it runs low, which our
    # auto-answer below handles, but a clear upfront warning helps.
    try:
        usage = shutil.disk_usage(tmp_dir)
        free_gb = usage.free / (1024 ** 3)
        _log(f"   Free space at destination: {free_gb:.1f} GB")
        if free_gb < 5:
            _log("   WARNING: Less than 5GB free — a dual-layer DVD rip "
                 "may not fit. Rip will continue and prompt may be "
                 "auto-answered, but the output could be truncated.")
    except Exception:
        pass

    # ── Stage 2 : MakeMKV rip ─────────────────────────────────────────
    _log("── Stage 2: Ripping with MakeMKV…")
    _progress(5, "Ripping DVD…")

    # NOTE: MakeMKV's "disc:N" uses its own internal drive INDEX (0, 1, 2...),
    # not the /dev/srN device path. On a Pi with a single optical drive this
    # is always disc:0. Using disc:/dev/srN with the standalone "mkv" command
    # triggers an "INVALID FIELD IN CDB" SCSI error on some HL-DT-ST drives
    # (exit code 10) — disc:0 with --directio=true works correctly.
    mkv_cmd = [
        MAKEMKV_BIN, "-r",
        "--decrypt",
        "--directio=true",
        "--minlength=300",   # skip titles under 5 minutes (extras/menus)
        "mkv",
        "disc:0",
        "all",          # all titles
        tmp_dir,
    ]
    _log(f"   $ {' '.join(mkv_cmd)}")

    try:
        proc = subprocess.Popen(
            mkv_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True, bufsize=1)
        mkv_progress_re = re.compile(r'PRGV:(\d+),(\d+),(\d+)')
        # MSG codes that present a yes/no confirmation prompt and block in
        # robot mode waiting on stdin (e.g. "low disk space, continue?").
        # Auto-answer "yes" so the rip proceeds unattended.
        mkv_confirm_re = re.compile(r'^MSG:(\d+),\d+,\d+,')
        # IDs known to require a response (disk space warning etc.)
        CONFIRM_MSG_IDS = {"5005", "5021", "5037"}
        for line in proc.stdout:
            line = line.rstrip()
            _log(line)
            # PRGV:current,total,max  — makemkvcon progress
            m = mkv_progress_re.search(line)
            if m:
                cur, tot, mx = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if mx > 0:
                    pct = 5 + int((cur / mx) * 40)   # 5-45%
                    _progress(pct, "Ripping DVD…")
            # Auto-answer confirmation prompts (e.g. low disk space)
            cm = mkv_confirm_re.match(line)
            if cm and cm.group(1) in CONFIRM_MSG_IDS:
                _log("   (auto-answering 'yes' to continue)")
                try:
                    proc.stdin.write("yes\n")
                    proc.stdin.flush()
                except Exception:
                    pass
        try:
            proc.stdin.close()
        except Exception:
            pass
        proc.wait()
        if proc.returncode not in (0, 1):   # MakeMKV returns 1 on soft errors
            raise RuntimeError(f"makemkvcon exited {proc.returncode}")
    except FileNotFoundError:
        msg = (f"{MAKEMKV_BIN} not found.\n"
               "Install: sudo apt install makemkv-bin\n"
               "Or build from source: https://www.makemkv.com/forum/viewtopic.php?t=224")
        if error_cb:
            error_cb(msg)
        return
    except Exception as e:
        if error_cb:
            error_cb(f"MakeMKV failed: {e}")
        return

    # Find the largest MKV (main feature)
    mkv_files = sorted(glob.glob(os.path.join(tmp_dir, "**", "*.mkv"),
                                 recursive=True),
                       key=os.path.getsize, reverse=True)
    if not mkv_files:
        if error_cb:
            error_cb("MakeMKV produced no MKV files. "
                     "Check libdvdcss2 is installed:\n"
                     "  sudo apt install libdvd-pkg\n"
                     "  sudo dpkg-reconfigure libdvd-pkg")
        return

    source_mkv = mkv_files[0]
    _log(f"   Source MKV: {source_mkv}  ({os.path.getsize(source_mkv)//1024//1024} MB)")
    _progress(48, "Rip complete. Transcoding…")

    # ── Stage 3 : HandBrakeCLI transcode ──────────────────────────────
    _log("── Stage 3: Transcoding with HandBrakeCLI…")

    # Final output folder
    movie_dir = os.path.join(output_dir, disc_title)
    os.makedirs(movie_dir, exist_ok=True)
    mp4_path  = os.path.join(movie_dir, f"{disc_title}.mp4")

    hb_cmd = ([HB_BIN, "-i", source_mkv, "-o", mp4_path]
              + HB_PRESET.split())
    _log(f"   $ {' '.join(hb_cmd)}")

    hb_progress_re = re.compile(
        r'Encoding: task \d+ of \d+, ([\d.]+) %')
    try:
        proc = subprocess.Popen(
            hb_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True, bufsize=1)
        for line in proc.stdout:
            line = line.rstrip()
            _log(line)
            m = hb_progress_re.search(line)
            if m:
                enc_pct = float(m.group(1))
                pct = 48 + int(enc_pct * 0.44)   # 48-92%
                _progress(pct, f"Encoding… {enc_pct:.0f}%")
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"HandBrakeCLI exited {proc.returncode}")
    except FileNotFoundError:
        msg = (f"{HB_BIN} not found.\n"
               "Install: sudo apt install handbrake-cli")
        if error_cb:
            error_cb(msg)
        return
    except Exception as e:
        if error_cb:
            error_cb(f"HandBrakeCLI failed: {e}")
        return

    _log(f"   MP4 saved: {mp4_path}")
    _progress(93, "Fetching cover art…")

    # ── Stage 4 : Cover art ───────────────────────────────────────────
    _log("── Stage 4: Cover art…")
    poster_data = None

    # NOTE: We do NOT extract a video frame as a poster. A raw DVD frame
    # is typically 16:9 or 4:3, but the video library grid expects a
    # 2:3 movie-poster aspect ratio (POSTER_W x POSTER_H). Resizing a
    # 16:9 frame into a 2:3 box stretches/distorts it badly. TMDb posters
    # are already correctly 2:3, so that's the only source we use.
    if tmdb_api_key:
        _log("   Poster: fetching from TMDb…")
        poster_data = _fetch_tmdb_poster(disc_title, tmdb_api_key)
        if poster_data:
            _log("   Poster: downloaded from TMDb")
        else:
            _log("   Poster: TMDb returned nothing")

    if poster_data:
        poster_path = _save_poster(movie_dir, poster_data)
        _log(f"   Poster saved: {poster_path}")
    else:
        _log("   No poster found — movie will show placeholder art")

    _progress(97, "Cleaning up…")

    # ── Stage 5 : Cleanup temp MKV ────────────────────────────────────
    _log("── Stage 5: Removing temp files…")
    try:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
        _log(f"   Removed: {tmp_dir}")
    except Exception as e:
        _log(f"   Cleanup warning: {e}")

    _progress(100, "Done!")
    _log(f"\n✓ Rip complete: {mp4_path}")
    if done_cb:
        done_cb(movie_dir)


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------

class DVDRipper(JukeboxModule):

    MODULE_ID          = "dvd_ripper"
    MODULE_NAME        = "DVD Ripper"
    MODULE_VERSION = APP_VERSION
    MODULE_DESCRIPTION = ("Rips DVDs to MP4 using MakeMKV + HandBrakeCLI. "
                          "Bypasses copy protection via libdvdcss2. "
                          "Fetches poster art from TMDb.")
    MODULE_AUTHOR      = "Andrew J. Morgan"

    def __init__(self):
        super().__init__()
        self._ripping        = False
        self._rip_thread: threading.Thread | None = None

        # UI refs
        self._status_var:   tk.StringVar | None  = None
        self._progress_var: tk.DoubleVar | None  = None
        self._stage_var:    tk.StringVar | None  = None
        self._log_text:     tk.Text | None       = None
        self._rip_btn:      tk.Button | None     = None
        self._device_var:   tk.StringVar | None  = None
        self._outdir_var:   tk.StringVar | None  = None
        self._title_var:    tk.StringVar | None  = None
        self._tmdb_var:     tk.StringVar | None  = None
        self._pbar:         ttk.Progressbar | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_load(self, app):
        super().on_load(app)
        print("[DVDRipper] Loaded")

    # ------------------------------------------------------------------
    # Rip entry point
    # ------------------------------------------------------------------

    def _start_rip(self):
        if self._ripping:
            return

        device  = self._device_var.get() if self._device_var else DEFAULT_DISC
        out_dir = os.path.expanduser(
            self._outdir_var.get() if self._outdir_var else "~/Videos")
        title   = (self._title_var.get()  if self._title_var  else "").strip()
        api_key = (self._tmdb_var.get()   if self._tmdb_var   else "").strip()

        if not os.path.exists(device):
            self._set_status(f"Device not found: {device}", error=True)
            return
        if not out_dir:
            self._set_status("Please choose an output directory", error=True)
            return

        os.makedirs(out_dir, exist_ok=True)

        # Persist settings
        self.config.set("dvd_output_dir", out_dir)
        self.config.set("tmdb_api_key",   api_key)

        self._ripping = True
        self._set_status("Ripping…")
        self._set_progress(0, "Starting…")
        if self._rip_btn:
            self.app.root.after(0, lambda: self._rip_btn.config(
                state="disabled", text="Ripping…"))

        def _log(line):
            if self._log_text:
                self.app.root.after(0, lambda l=line: self._append_log(l))

        def _progress(pct, stage):
            self.app.root.after(0, lambda p=pct, s=stage:
                                self._set_progress(p, s))

        def _done(movie_dir):
            self._ripping = False
            self._set_status("✓ Rip complete!", error=False)
            self._set_progress(100, "Done!")
            self.emit_notification(
                f"DVD ripped: {os.path.basename(movie_dir)}", "success")
            if self._rip_btn:
                self.app.root.after(0, lambda: self._rip_btn.config(
                    state="normal", text="▶  Rip DVD"))
            # Refresh video library
            if self.app and hasattr(self.app, "refresh_video_library"):
                self.app.root.after(500, self.app.refresh_video_library)

        def _error(msg):
            self._ripping = False
            self._set_status(f"Error: {msg[:80]}", error=True)
            self._set_progress(0, "Failed")
            self.emit_notification("DVD rip failed — see log", "error")
            if self._rip_btn:
                self.app.root.after(0, lambda: self._rip_btn.config(
                    state="normal", text="▶  Rip DVD"))

        self._rip_thread = threading.Thread(
            target=rip_dvd,
            args=(device, out_dir),
            kwargs=dict(title_override=title, tmdb_api_key=api_key,
                        log_cb=_log, progress_cb=_progress,
                        done_cb=_done, error_cb=_error),
            daemon=True)
        self._rip_thread.start()

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------

    def _set_status(self, msg: str, error: bool = False):
        if self._status_var and self.app:
            self.app.root.after(0, lambda m=msg: self._status_var.set(m))

    def _set_progress(self, pct: int, stage: str):
        if self._progress_var and self.app:
            self.app.root.after(0, lambda p=pct: self._progress_var.set(p))
        if self._stage_var and self.app:
            self.app.root.after(0, lambda s=stage: self._stage_var.set(s))

    def _append_log(self, line: str):
        if self._log_text:
            self._log_text.config(state="normal")
            self._log_text.insert("end", line)
            self._log_text.see("end")
            self._log_text.config(state="disabled")

    # ------------------------------------------------------------------
    # MakeMKV key helpers
    # ------------------------------------------------------------------

    def _makemkv_conf_path(self) -> str:
        return os.path.expanduser("~/.MakeMKV/settings.conf")

    def _read_makemkv_key(self) -> str:
        """Read the current beta key from ~/.MakeMKV/settings.conf."""
        try:
            with open(self._makemkv_conf_path()) as f:
                for line in f:
                    if line.startswith("app_Key"):
                        return line.split("=", 1)[1].strip().strip('"')
        except FileNotFoundError:
            pass
        return ""

    def _save_makemkv_key(self):
        """Write the entered key to ~/.MakeMKV/settings.conf."""
        key = self._mkv_key_var.get().strip() if self._mkv_key_var else ""
        if not key:
            self.emit_notification("Enter a key first", "warning")
            return
        conf_path = self._makemkv_conf_path()
        os.makedirs(os.path.dirname(conf_path), exist_ok=True)
        # Read existing settings, replace or add app_Key
        lines = []
        found = False
        try:
            with open(conf_path) as f:
                lines = f.readlines()
        except FileNotFoundError:
            pass
        new_lines = []
        for line in lines:
            if line.startswith("app_Key"):
                new_lines.append(f"app_Key = \"{key}\"\n")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"app_Key = \"{key}\"\n")
        with open(conf_path, "w") as f:
            f.writelines(new_lines)
        self.emit_notification("MakeMKV key saved", "success")

    def _fetch_makemkv_key(self):
        """Fetch the current beta key from cable.ayra.ch API."""
        import threading, urllib.request, re
        self.emit_notification("Fetching MakeMKV key…", "info")

        def _fetch():
            sources = [
                "https://cable.ayra.ch/makemkv/api/api.php?f=txt",
                "https://forum.makemkv.com/forum/viewtopic.php?t=1053",
            ]
            key = ""
            for url in sources:
                try:
                    req = urllib.request.Request(
                        url,
                        headers={"User-Agent":
                            "Mozilla/5.0 (X11; Linux aarch64; rv:109.0) "
                            "Gecko/20100101 Firefox/115.0"})
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        text = resp.read().decode("utf-8", errors="ignore")
                    match = re.search(r"T-[A-Za-z0-9_@-]{20,}", text)
                    if match:
                        key = match.group(0)
                        break
                except Exception as e:
                    print(f"[DVDRipper] Key fetch from {url}: {e}")

            if self.app:
                self.app.root.after(0, lambda k=key: self._on_key_fetched(k))

        threading.Thread(target=_fetch, daemon=True).start()

    def _on_key_fetched(self, key: str):
        if key:
            if self._mkv_key_var:
                self._mkv_key_var.set(key)
            self._save_makemkv_key()
            self.emit_notification(f"Key updated: {key[:16]}…", "success")
        else:
            self.emit_notification(
                "Could not fetch key — check internet connection", "error")

    def _browse_output(self):
        cur = os.path.expanduser(
            self._outdir_var.get() if self._outdir_var else "~/Videos")
        chosen = filedialog.askdirectory(
            title="Select Video Output Directory",
            initialdir=cur if os.path.isdir(cur) else os.path.expanduser("~"))
        if chosen and self._outdir_var:
            self._outdir_var.set(chosen)

    def _refresh_drives(self):
        drives = _scan_drives()
        if hasattr(self, "_dev_combo"):
            self._dev_combo["values"] = drives
            if self._device_var and self._device_var.get() not in drives:
                self._device_var.set(drives[0] if drives else DEFAULT_DISC)

    def _probe_disc(self):
        device = self._device_var.get() if self._device_var else DEFAULT_DISC
        self._set_status("Reading disc title…")
        def _run():
            title = _disc_title_from_makemkv(device)
            def _ui():
                if title and self._title_var:
                    self._title_var.set(title)
                self._set_status(f"Title: {title}" if title
                                 else "Could not read title — enter manually")
            if self.app:
                self.app.root.after(0, _ui)
        threading.Thread(target=_run, daemon=True).start()

    # ------------------------------------------------------------------
    # UI Panel
    # ------------------------------------------------------------------

    def get_ui_panel(self, parent) -> tk.Frame:
        frame = tk.Frame(parent, bg=BG_PANEL)

        # ── Tool availability ─────────────────────────────────────────
        missing = []
        for tool, pkg in [(MAKEMKV_BIN, "makemkv-bin"),
                          (HB_BIN,      "handbrake-cli"),
                          ("ffmpeg",    "ffmpeg")]:
            if not _which(tool):
                missing.append((tool, pkg))

        if missing:
            warn = tk.Frame(frame, bg="#2a1000", padx=8, pady=6)
            warn.pack(fill="x", pady=(0, 8))
            tk.Label(warn, text="⚠  Missing tools:",
                     font=FONT_SMALL, fg=WARNING, bg="#2a1000").pack(anchor="w")
            for tool, pkg in missing:
                tk.Label(warn, text=f"   sudo apt install {pkg}",
                         font=FONT_TINY, fg=TEXT_PRIMARY, bg="#2a1000").pack(anchor="w")
            tk.Label(warn,
                     text="   sudo apt install libdvd-pkg && "
                          "sudo dpkg-reconfigure libdvd-pkg",
                     font=FONT_TINY, fg=TEXT_PRIMARY, bg="#2a1000").pack(anchor="w")

        # ── Device row ────────────────────────────────────────────────
        def _row(lbl):
            r = tk.Frame(frame, bg=BG_PANEL)
            r.pack(fill="x", pady=2)
            tk.Label(r, text=lbl, font=FONT_SMALL,
                     fg=TEXT_PRIMARY, bg=BG_PANEL,
                     width=12, anchor="w").pack(side="left")
            return r

        r1 = _row("Drive:")
        self._device_var = tk.StringVar(
            value=_find_dvd_device() or DEFAULT_DISC)
        self._dev_combo  = ttk.Combobox(r1, textvariable=self._device_var,
                                        font=FONT_SMALL, width=14, state="normal")
        self._dev_combo["values"] = _scan_drives()
        self._dev_combo.pack(side="left", padx=(0, 6), ipady=2)
        tk.Button(r1, text="⟳ Scan", font=FONT_TINY,
                  fg=ACCENT, bg=BG_CARD, relief="flat", bd=0,
                  cursor="hand2", padx=6, pady=2,
                  command=self._refresh_drives).pack(side="left", padx=(0, 6))
        tk.Button(r1, text="Read Title", font=FONT_TINY,
                  fg=NEON_BLUE, bg=BG_CARD, relief="flat", bd=0,
                  cursor="hand2", padx=6, pady=2,
                  command=self._probe_disc).pack(side="left")

        r2 = _row("Title:")
        self._title_var = tk.StringVar(value="")
        tk.Entry(r2, textvariable=self._title_var,
                 font=FONT_SMALL, bg=BG_CARD, fg=TEXT_PRIMARY,
                 insertbackground=ACCENT, relief="flat",
                 width=30).pack(side="left", padx=(0, 6), ipady=3)
        tk.Label(r2, text="(leave blank to auto-detect)",
                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_PANEL).pack(side="left")

        r3 = _row("Output dir:")
        self._outdir_var = tk.StringVar(
            value=self.config.get("dvd_output_dir",
                                  os.path.expanduser("~/Videos")))
        tk.Entry(r3, textvariable=self._outdir_var,
                 font=FONT_SMALL, bg=BG_CARD, fg=TEXT_PRIMARY,
                 insertbackground=ACCENT, relief="flat",
                 width=26).pack(side="left", padx=(0, 6), ipady=3)
        tk.Button(r3, text="Browse…", font=FONT_TINY,
                  fg=ACCENT, bg=BG_CARD, relief="flat", bd=0,
                  cursor="hand2", padx=6, pady=2,
                  command=self._browse_output).pack(side="left")

        r4 = _row("TMDb key:")
        self._tmdb_var = tk.StringVar(
            value=self.config.get("tmdb_api_key", ""))
        tk.Entry(r4, textvariable=self._tmdb_var,
                 font=FONT_SMALL, bg=BG_CARD, fg=TEXT_PRIMARY,
                 insertbackground=ACCENT, relief="flat",
                 show="•", width=30).pack(side="left", padx=(0, 6), ipady=3)
        tk.Label(r4, text="themoviedb.org/settings/api",
                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_PANEL).pack(side="left")

        # ── MakeMKV beta key ──────────────────────────────────────────
        r5 = _row("MakeMKV key:")
        self._mkv_key_var = tk.StringVar(
            value=self._read_makemkv_key())
        tk.Entry(r5, textvariable=self._mkv_key_var,
                 font=FONT_SMALL, bg=BG_CARD, fg=TEXT_PRIMARY,
                 insertbackground=ACCENT, relief="flat",
                 width=30).pack(side="left", padx=(0, 6), ipady=3)
        tk.Button(r5, text="Save Key", font=FONT_TINY,
                  fg=BG_PANEL, bg=ACCENT, relief="flat", bd=0,
                  cursor="hand2", padx=6, pady=2,
                  command=self._save_makemkv_key).pack(side="left", padx=(0, 4))
        tk.Button(r5, text="⟳ Fetch Latest", font=FONT_TINY,
                  fg=BG_PANEL, bg=SUCCESS, relief="flat", bd=0,
                  cursor="hand2", padx=6, pady=2,
                  command=self._fetch_makemkv_key).pack(side="left", padx=(0, 4))
        tk.Label(r5, text="cable.ayra.ch/makemkv",
                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_PANEL).pack(side="left")

        # ── Progress bar ──────────────────────────────────────────────
        tk.Frame(frame, bg=BORDER, height=1).pack(fill="x", pady=(10, 6))

        self._stage_var    = tk.StringVar(value="Ready")
        self._status_var   = tk.StringVar(value="Insert disc and press Rip DVD")
        self._progress_var = tk.DoubleVar(value=0)

        tk.Label(frame, textvariable=self._stage_var,
                 font=FONT_SMALL, fg=ACCENT, bg=BG_PANEL).pack(anchor="w")

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("DVD.Horizontal.TProgressbar",
                        background=ACCENT, troughcolor=BG_DARK,
                        borderwidth=0, lightcolor=ACCENT,
                        darkcolor=ACCENT)
        self._pbar = ttk.Progressbar(
            frame, variable=self._progress_var,
            maximum=100, length=400, mode="determinate",
            style="DVD.Horizontal.TProgressbar")
        self._pbar.pack(fill="x", pady=(2, 4))

        tk.Label(frame, textvariable=self._status_var,
                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_PANEL,
                 anchor="w").pack(fill="x")

        # ── Buttons ───────────────────────────────────────────────────
        btn_row = tk.Frame(frame, bg=BG_PANEL)
        btn_row.pack(fill="x", pady=(10, 6))

        self._rip_btn = tk.Button(
            btn_row, text="▶  Rip DVD",
            font=FONT_SUBHEAD, fg=BG_PANEL, bg=ACCENT,
            relief="flat", bd=0, cursor="hand2", padx=18, pady=8,
            command=self._start_rip)
        self._rip_btn.pack(side="left", padx=(0, 8))

        tk.Button(btn_row, text="Eject",
                  font=FONT_SMALL, fg=TEXT_PRIMARY, bg=BG_CARD,
                  relief="flat", bd=0, cursor="hand2", padx=10, pady=8,
                  command=lambda: subprocess.Popen(
                      ["eject", self._device_var.get()])).pack(side="left")

        # ── Log ───────────────────────────────────────────────────────
        tk.Frame(frame, bg=BORDER, height=1).pack(fill="x", pady=(8, 4))
        tk.Label(frame, text="Rip log:", font=FONT_TINY,
                 fg=TEXT_PRIMARY, bg=BG_PANEL).pack(anchor="w")

        log_wrap = tk.Frame(frame, bg=BG_DARK)
        log_wrap.pack(fill="both", expand=True)
        self._log_text = tk.Text(
            log_wrap, height=10,
            font=("Courier", 8), bg="#050505", fg="#88ff88",
            insertbackground=ACCENT, state="disabled",
            relief="flat", wrap="word")
        self._log_text.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(log_wrap, command=self._log_text.yview,
                          bg=BG_DARK, troughcolor=BG_DARK)
        sb.pack(side="right", fill="y")
        self._log_text.config(yscrollcommand=sb.set)

        tk.Button(frame, text="Clear log", font=FONT_TINY,
                  fg=TEXT_PRIMARY, bg=BG_PANEL, relief="flat", bd=0,
                  cursor="hand2",
                  command=lambda: (
                      self._log_text.config(state="normal"),
                      self._log_text.delete("1.0", "end"),
                      self._log_text.config(state="disabled")
                  )).pack(anchor="e", pady=(2, 0))

        return frame
