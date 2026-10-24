"""
PulseBox Module: AirPlay 2 Receiver
===================================
Turns PulseBox into an AirPlay 2 receiver using **Shairport Sync**. When an
Apple device streams to it:

  * PulseBox detects the stream (via Shairport Sync's metadata pipe),
  * pauses local playback and switches to a full-screen Now Playing display
    with album art and track metadata, and
  * returns to the PulseBox home screen automatically when streaming stops.

Transport controls are deliberately not offered: macOS streams in AirPlay 2
"Buffered" mode and doesn't supply the DACP credentials (daid/acre) that
Shairport Sync needs to relay commands back to the sender, so such buttons
could never work. Use the controls on the source device.

Shairport Sync handles the actual audio — it plays straight to the sound
device. This module only reacts to it. AirPlay 2 support is NOT an apt package;
Shairport Sync (and its companion nqptp) must be built from source with
--with-airplay-2 --with-metadata. Use the helper:

    bash scripts/install_airplay.sh

which builds and configures everything, including the metadata pipe this
module reads.
"""

import base64
import os
import re
import select
import shutil
import subprocess
import threading
import time
import tkinter as tk

from core.config import JukeboxModule, APP_VERSION

DEFAULT_PIPE = "/tmp/shairport-sync-metadata"


def _is_image(data: bytes) -> bool:
    """True if the payload starts with JPEG/PNG/GIF/BMP magic bytes."""
    if len(data) < 8:
        return False
    return (data[:3] == b"\xff\xd8\xff"                 # JPEG
            or data[:8] == b"\x89PNG\r\n\x1a\n"          # PNG
            or data[:6] in (b"GIF87a", b"GIF89a")        # GIF
            or data[:2] == b"BM")                        # BMP


def _parse_bplist(data: bytes):
    """
    Parse an Apple binary plist payload ('bplist00'), returning
    (image_bytes|None, {meta}) — some senders (notably macOS in AirPlay 2
    'Buffered' mode) wrap cover art and track metadata in a plist instead of
    sending discrete minm/asar/asal/PICT items.
    """
    if not data[:8] == b"bplist00":
        return (None, {})
    try:
        import plistlib
        obj = plistlib.loads(data, fmt=plistlib.FMT_BINARY)
    except Exception as e:
        print(f"[AirPlay] bplist parse failed: {e}")
        return (None, {})

    image = None
    meta = {}
    _KEYS = {
        "title": "title", "name": "title", "itemname": "title",
        "artist": "artist", "albumartist": "artist",
        "album": "album", "albumname": "album",
    }

    def walk(o):
        nonlocal image
        if isinstance(o, dict):
            for k, v in o.items():
                kl = str(k).lower().replace("_", "").replace(" ", "")
                if isinstance(v, (bytes, bytearray)):
                    if image is None and _is_image(bytes(v)):
                        image = bytes(v)
                elif isinstance(v, str) and kl in _KEYS and v.strip():
                    meta.setdefault(_KEYS[kl], v.strip())
                else:
                    walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)
        elif isinstance(o, (bytes, bytearray)):
            if image is None and _is_image(bytes(o)):
                image = bytes(o)

    walk(obj)
    return (image, meta)


def _extract_image(data: bytes):
    """
    Return embedded image bytes from a payload, or None.

    Cover art is sometimes wrapped in a container rather than sent as a bare
    JPEG, so as well as checking the start, scan for an image signature inside
    the payload and slice from there. Only worth trying on sizeable payloads.
    """
    if _is_image(data):
        return data
    if len(data) < 2000:
        return None
    for magic in (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n"):
        idx = data.find(magic)
        # Must be a real embedded image, not a chance byte match near the end
        if idx > 0 and (len(data) - idx) > 1000:
            return data[idx:]
    return None

# Items are parsed by locating <item>...</item> boundaries in the raw byte
# stream (see _consume_items/_parse_block). The pipe carries binary cover art,
# so it must never be decoded as text.


def _shairport_info():
    """Return (installed, airplay2, version_string)."""
    p = shutil.which("shairport-sync")
    if not p:
        return (False, False, "")
    try:
        out = subprocess.run([p, "-V"], capture_output=True, text=True,
                             timeout=5).stdout.strip()
    except Exception:
        out = ""
    airplay2 = ("airplay2" in out.lower()) or ("airplay-2" in out.lower())
    return (True, airplay2, out)


class AirPlayReceiverModule(JukeboxModule):

    MODULE_ID          = "airplay_receiver"
    MODULE_NAME        = "AirPlay 2 Receiver"
    MODULE_VERSION     = APP_VERSION
    MODULE_DESCRIPTION = (
        "Receive AirPlay 2 streams from Apple devices via Shairport Sync, with "
        "a full-screen Now Playing display with album art and track metadata. "
        "Requires Shairport Sync built with AirPlay 2 (see scripts/install_airplay.sh).")
    MODULE_AUTHOR      = "PulseBox"

    def __init__(self):
        super().__init__()
        self._reading      = False
        self._reader       = None
        self._streaming    = False
        self._meta         = {}          # title / artist / album / client
        self._seen_codes   = set()       # distinct pipe codes seen (diagnostic)
        self._art_data     = None        # newest art bytes (survives overlay)
        self._overlay      = None
        self._art_label    = None
        self._art_photo    = None
        self._lbl_title    = None
        self._lbl_artist   = None
        self._lbl_album    = None
        self._lbl_client   = None
        # UI-panel widgets
        self._status_var   = None
        self._enabled_var  = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_load(self, app):
        super().on_load(app)
        # Don't spin up the receiver (and its pipe-wait) if there's no working
        # sound card — Shairport can't play, so there'd be nothing but noise.
        if getattr(getattr(app, "player", None), "no_audio_device", False):
            self._set_status("No audio device - AirPlay disabled")
            print("[AirPlay] No audio device detected - receiver not started.")
            return
        if self.config.get("airplay_enabled", False):
            installed, _, _ = _shairport_info()
            if installed:
                self._start_receiver()

    def on_unload(self):
        self._stop_receiver()
        super().on_unload()

    # ------------------------------------------------------------------
    # Receiver control
    # ------------------------------------------------------------------

    def _pipe_path(self) -> str:
        return self.config.get("airplay_metadata_pipe", DEFAULT_PIPE)

    def _start_receiver(self):
        # Best-effort: make sure the Shairport Sync service is running. Uses
        # passwordless sudo where available; harmless otherwise.
        try:
            subprocess.run(["sudo", "-n", "systemctl", "start",
                            "shairport-sync"], timeout=10,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
        if self._reading:
            return
        self._reading = True
        self._reader = threading.Thread(target=self._read_pipe, daemon=True)
        self._reader.start()
        self._set_status("Listening for AirPlay streams")

    def _stop_receiver(self):
        self._reading = False
        # Tear down any visible overlay
        if self.app:
            try:
                self.app.root.after(0, self._hide_overlay)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Metadata pipe reader (background thread)
    # ------------------------------------------------------------------

    def _read_pipe(self):
        pipe = self._pipe_path()
        buf = b""
        while self._reading:
            if not os.path.exists(pipe):
                self._set_status(
                    "Waiting for Shairport Sync metadata pipe...")
                time.sleep(3)
                continue
            try:
                fd = os.open(pipe, os.O_RDONLY | os.O_NONBLOCK)
            except Exception:
                time.sleep(3)
                continue
            try:
                while self._reading:
                    r, _, _ = select.select([fd], [], [], 1.0)
                    if not r:
                        continue
                    try:
                        chunk = os.read(fd, 8192)
                    except BlockingIOError:
                        continue
                    except Exception:
                        break
                    if not chunk:
                        # No writer right now — avoid a busy loop
                        time.sleep(0.5)
                        continue
                    # Accumulate as BYTES. Decoding chunks as UTF-8 silently
                    # drops invalid bytes (cover art is raw binary), which
                    # shifts everything after it and corrupts item codes.
                    buf += chunk
                    buf = self._consume_items(buf)
                    if len(buf) > 4_000_000:      # runaway guard
                        buf = buf[-8192:]
            finally:
                try:
                    os.close(fd)
                except Exception:
                    pass

    def _consume_items(self, buf: bytes) -> bytes:
        # Work on BYTES: cover art is raw binary and must never pass through a
        # text decode. base64/XML markers are ASCII, so byte-level find() on
        # <item>...</item> is exact and safe.
        while True:
            start = buf.find(b"<item>")
            if start == -1:
                return buf[-16:] if len(buf) > 16 else buf
            end = buf.find(b"</item>", start)
            if end == -1:
                return buf[start:]          # incomplete item — wait for more
            block = buf[start:end + len(b"</item>")]
            buf = buf[end + len(b"</item>"):]
            self._parse_block(block)

    def _parse_block(self, block: bytes):
        cm = re.search(rb"<code>([0-9a-fA-F]+)</code>", block)
        if not cm:
            return
        tm = re.search(rb"<type>([0-9a-fA-F]+)</type>", block)
        try:
            code = bytes.fromhex(cm.group(1).decode("ascii")).decode(
                "ascii", "ignore")
        except Exception:
            return
        typ = ""
        if tm:
            try:
                typ = bytes.fromhex(tm.group(1).decode("ascii")).decode(
                    "ascii", "ignore")
            except Exception:
                typ = ""
        data = b""
        dm = re.search(rb'<data encoding="base64">\s*(.*?)\s*</data>',
                       block, re.DOTALL)
        if dm and dm.group(1):
            try:
                data = base64.b64decode(dm.group(1))
            except Exception:
                data = b""

        if (typ, code) not in self._seen_codes:
            self._seen_codes.add((typ, code))
            extra = ""
            if data:
                if _is_image(data):
                    extra = f" [IMAGE {len(data)} bytes]"
                elif len(data) > 2000:
                    head = data[:16].hex(" ")
                    ascii_head = "".join(
                        chr(b) if 32 <= b < 127 else "." for b in data[:16])
                    extra = (f" ({len(data)} bytes) first16=[{head}] "
                             f"'{ascii_head}'")
                elif len(data) < 120:
                    txt = data.decode("utf-8", "ignore").strip()
                    if txt.isprintable():
                        extra = f" = {txt!r}"
                    else:
                        extra = f" ({len(data)} bytes)"
                else:
                    extra = f" ({len(data)} bytes)"
            else:
                extra = " (no data)"
            print(f"[AirPlay] item {typ}/{code}{extra}")

        self._handle_item(code, data)

        # Content-based fallback: identify art by what it *is*, not by code
        # name — builds differ, and art may be wrapped in a container.
        if data and code != "PICT":
            # macOS buffered-mode wraps art (and often the track metadata) in
            # an Apple binary plist — parse it properly rather than just
            # scavenging the image out of it.
            if data[:8] == b"bplist00":
                img, pmeta = _parse_bplist(data)
                if pmeta:
                    for k, v in pmeta.items():
                        if self._meta.get(k) != v:
                            self._meta[k] = v
                            print(f"[AirPlay] {k}: {v}")
                    self._push_meta()
                if img:
                    print(f"[AirPlay] cover art from plist in '{code}' "
                          f"({len(img)} bytes)")
                    self._on_art(img)
                    return
            img = _extract_image(data)
            if img:
                where = "embedded in" if not _is_image(data) else "in"
                print(f"[AirPlay] cover art {where} '{code}' item "
                      f"({len(img)} bytes)")
                self._on_art(img)

    def _handle_item(self, code: str, data: bytes):
        if code == "pbeg":
            self._on_airplay_start()
        elif code in ("pend", "pfls"):
            if code == "pend":
                self._on_airplay_stop()
        elif code == "PICT":
            print(f"[AirPlay] PICT (cover art) received: {len(data)} bytes")
            if data:
                self._on_art(data)
        elif code == "minm":
            self._meta["title"] = data.decode("utf-8", "ignore")
            print(f"[AirPlay] title: {self._meta['title']}")
            self._push_meta()
        elif code == "asar":
            self._meta["artist"] = data.decode("utf-8", "ignore")
            self._push_meta()
        elif code == "asal":
            self._meta["album"] = data.decode("utf-8", "ignore")
            self._push_meta()
        elif code in ("snam", "snua"):
            self._meta["client"] = data.decode("utf-8", "ignore")
            self._push_meta()
        elif code == "mden":
            # End of a metadata bundle. Not all builds emit this, so it's only
            # an extra refresh — never the sole trigger for updating the UI.
            self._push_meta()

    # ------------------------------------------------------------------
    # AirPlay start / stop  (all UI work marshalled to the main thread)
    # ------------------------------------------------------------------

    def _on_airplay_start(self):
        self._streaming = True
        self._seen_codes = set()          # fresh log of codes for this session
        self._set_status("Streaming from AirPlay")

        def _ui():
            if self.config.get("airplay_pause_local", True):
                try:
                    # STOP (not pause) so VLC releases the ALSA device — a
                    # paused VLC keeps the DAC open, which blocks Shairport
                    # from opening it on a single-card setup.
                    self.app.player.stop()
                except Exception:
                    pass
            self._show_overlay()
        self._after(_ui)

    def _on_airplay_stop(self):
        self._streaming = False
        self._meta = {}
        self._art_data = None            # don't carry art into the next session
        self._set_status("Listening for AirPlay streams")

        def _ui():
            self._hide_overlay()
            if self.config.get("airplay_return_home", True):
                try:
                    self.app.show_page("albums")
                except Exception:
                    pass
        self._after(_ui)

    def _on_art(self, data: bytes):
        # Always keep the newest art bytes, even if the overlay doesn't exist
        # yet — art can arrive before pbeg. _show_overlay() re-applies it.
        self._art_data = data

        def _ui():
            if not (self._overlay and self._art_label):
                return
            try:
                from PIL import Image, ImageTk
                import io
                img = Image.open(io.BytesIO(data)).convert("RGB")
                img.thumbnail((360, 360), Image.Resampling.LANCZOS)
                self._art_photo = ImageTk.PhotoImage(img)
                self._art_label.config(image=self._art_photo, text="")
            except Exception as e:
                print(f"[AirPlay] art decode failed: {e}")
        self._after(_ui)

    def _push_meta(self):
        def _ui():
            if not self._overlay:
                return
            if self._lbl_title:
                self._lbl_title.config(text=self._meta.get("title", ""))
            if self._lbl_artist:
                self._lbl_artist.config(text=self._meta.get("artist", ""))
            if self._lbl_album:
                self._lbl_album.config(text=self._meta.get("album", ""))
            if self._lbl_client:
                c = self._meta.get("client", "")
                self._lbl_client.config(
                    text=f"AirPlay{' — ' + c if c else ''}")
        self._after(_ui)

    def _after(self, fn):
        if self.app and getattr(self.app, "root", None):
            try:
                self.app.root.after(0, fn)
                return
            except Exception:
                pass
        fn()

    # ------------------------------------------------------------------
    # Full-screen Now Playing overlay
    # ------------------------------------------------------------------

    def _show_overlay(self):
        if self._overlay and self._overlay.winfo_exists():
            self._overlay.lift()
            self._push_meta()
            return
        from ui.theme import (BG_DARK, BG_CARD, ACCENT, NEON_BLUE,
                              TEXT_PRIMARY, TEXT_SECONDARY, FONT_DISPLAY,
                              FONT_HEADING, FONT_BODY, FONT_SMALL)

        # Sit in the content area (between the nav bar and the player bar),
        # not over the whole window.
        parent = getattr(self.app, "_content_area", None) or self.app.root
        ov = tk.Frame(parent, bg=BG_DARK)
        ov.place(x=0, y=0, relwidth=1, relheight=1)
        ov.lift()

        wrap = tk.Frame(ov, bg=BG_DARK)
        wrap.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(wrap, text=" AirPlay", font=FONT_SMALL,
                 fg=NEON_BLUE, bg=BG_DARK).pack(pady=(0, 10))

        # Fixed-size art holder. The label's width/height are in *pixels* once
        # an image is set, so pin the holder and let the label fill it — this
        # avoids the image being clamped to a few text-cell units.
        art_holder = tk.Frame(wrap, bg=BG_CARD, width=360, height=360)
        art_holder.pack(pady=(0, 18))
        art_holder.pack_propagate(False)
        self._art_label = tk.Label(art_holder, text="♪",
                                   font=("Helvetica", 96),
                                   fg=BG_DARK, bg=BG_CARD)
        self._art_label.pack(fill="both", expand=True)

        self._lbl_title = tk.Label(wrap, text="", font=FONT_DISPLAY,
                                   fg=TEXT_PRIMARY, bg=BG_DARK,
                                   wraplength=600)
        self._lbl_title.pack()
        self._lbl_artist = tk.Label(wrap, text="", font=FONT_HEADING,
                                    fg=ACCENT, bg=BG_DARK, wraplength=600)
        self._lbl_artist.pack(pady=(4, 0))
        self._lbl_album = tk.Label(wrap, text="", font=FONT_BODY,
                                   fg=TEXT_SECONDARY, bg=BG_DARK,
                                   wraplength=600)
        self._lbl_album.pack(pady=(2, 0))

        # No transport controls: macOS streams in AirPlay 2 "Buffered" mode and
        # doesn't provide the DACP credentials (daid/acre) Shairport Sync needs
        # to relay commands back to the sender, so the buttons could never do
        # anything. Use the controls on the source device instead.

        self._lbl_client = tk.Label(ov, text="AirPlay", font=FONT_SMALL,
                                    fg=TEXT_SECONDARY, bg=BG_DARK)
        self._lbl_client.place(relx=0.5, rely=0.94, anchor="center")

        self._overlay = ov
        self._push_meta()
        # Re-apply artwork that arrived before this overlay existed
        if self._art_data:
            self._on_art(self._art_data)

    def _hide_overlay(self):
        if self._overlay:
            try:
                self._overlay.destroy()
            except Exception:
                pass
        self._overlay = None
        self._art_label = self._art_photo = None
        self._lbl_title = self._lbl_artist = None
        self._lbl_album = self._lbl_client = None

    # ------------------------------------------------------------------
    # Status helper
    # ------------------------------------------------------------------

    def _set_status(self, msg: str, error: bool = False):
        print(f"[AirPlay] {'ERROR' if error else 'OK'}: {msg}")
        if self._status_var and self.app:
            try:
                self.app.root.after(0, lambda: self._status_var.set(msg))
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Settings toggle
    # ------------------------------------------------------------------

    def _toggle_enabled(self):
        enabled = bool(self._enabled_var.get())
        installed, airplay2, _ = _shairport_info()
        if enabled and not installed:
            self._set_status(
                "Shairport Sync not installed - run scripts/install_airplay.sh",
                error=True)
            self._enabled_var.set(False)
            self.config.set("airplay_enabled", False)
            return
        self.config.set("airplay_enabled", enabled)
        if enabled:
            if not airplay2:
                self._set_status(
                    "Shairport Sync is installed but without AirPlay 2 - "
                    "rebuild with --with-airplay-2")
            self._start_receiver()
        else:
            self._stop_receiver()
            try:
                subprocess.run(["sudo", "-n", "systemctl", "stop",
                                "shairport-sync"], timeout=10,
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
            except Exception:
                pass
            self._set_status("Stopped")

    # ------------------------------------------------------------------
    # UI panel
    # ------------------------------------------------------------------

    def get_ui_panel(self, parent) -> tk.Frame:
        from ui.theme import (BG_PANEL, BG_CARD, ACCENT, NEON_BLUE, DANGER,
                              WARNING, TEXT_PRIMARY, TEXT_SECONDARY,
                              FONT_BODY, FONT_SMALL, FONT_TINY)

        frame = tk.Frame(parent, bg=BG_PANEL)
        installed, airplay2, version = _shairport_info()

        top = tk.Frame(frame, bg=BG_PANEL)
        top.pack(fill="x", pady=(0, 6))
        self._enabled_var = tk.BooleanVar(
            value=self.config.get("airplay_enabled", False))
        tk.Checkbutton(top, text="Enable AirPlay 2 receiver",
                       variable=self._enabled_var, font=FONT_BODY,
                       fg=TEXT_PRIMARY, bg=BG_PANEL, activebackground=BG_PANEL,
                       selectcolor=BG_CARD, cursor="hand2",
                       command=self._toggle_enabled).pack(side="left")

        if not installed:
            state = "Shairport Sync not installed"
        elif not airplay2:
            state = "Installed (AirPlay 1 only - rebuild for AirPlay 2)"
        elif self._reading:
            state = "Listening for AirPlay streams"
        else:
            state = "Ready"
        self._status_var = tk.StringVar(value=state)
        tk.Label(frame, textvariable=self._status_var, font=FONT_SMALL,
                 fg=NEON_BLUE, bg=BG_PANEL).pack(anchor="w", pady=(0, 8))

        if not installed:
            tk.Label(frame,
                     text="AirPlay 2 isn't an apt package. Build Shairport "
                          "Sync + nqptp with:\n"
                          "    bash scripts/install_airplay.sh\n"
                          "(takes a while — it compiles from source).",
                     font=FONT_TINY, fg=DANGER, bg=BG_PANEL,
                     justify="left").pack(anchor="w", pady=(0, 8))
        elif not airplay2:
            tk.Label(frame,
                     text="This Shairport Sync build lacks AirPlay 2. Re-run "
                          "scripts/install_airplay.sh to rebuild it.",
                     font=FONT_TINY, fg=WARNING, bg=BG_PANEL,
                     justify="left").pack(anchor="w", pady=(0, 8))

        # Behaviour options
        self._opt_pause = tk.BooleanVar(
            value=self.config.get("airplay_pause_local", True))
        self._opt_home = tk.BooleanVar(
            value=self.config.get("airplay_return_home", True))
        tk.Checkbutton(frame, text="Pause local playback while streaming",
                       variable=self._opt_pause, font=FONT_SMALL,
                       fg=TEXT_PRIMARY, bg=BG_PANEL, activebackground=BG_PANEL,
                       selectcolor=BG_CARD, cursor="hand2",
                       command=lambda: self.config.set(
                           "airplay_pause_local",
                           bool(self._opt_pause.get()))).pack(anchor="w")
        tk.Checkbutton(frame, text="Return to home screen when streaming stops",
                       variable=self._opt_home, font=FONT_SMALL,
                       fg=TEXT_PRIMARY, bg=BG_PANEL, activebackground=BG_PANEL,
                       selectcolor=BG_CARD, cursor="hand2",
                       command=lambda: self.config.set(
                           "airplay_return_home",
                           bool(self._opt_home.get()))).pack(anchor="w")

        if version:
            tk.Label(frame, text=version, font=FONT_TINY, fg=TEXT_SECONDARY,
                     bg=BG_PANEL, wraplength=460,
                     justify="left").pack(anchor="w", pady=(8, 0))

        tk.Label(frame,
                 text="Once enabled, pick 'PulseBox' from the AirPlay menu on "
                      "your iPhone, iPad or Mac. The audio plays through your "
                      "sound card; PulseBox shows a full-screen Now Playing "
                      "display and returns home when you stop.",
                 font=FONT_TINY, fg=TEXT_SECONDARY, bg=BG_PANEL,
                 wraplength=460, justify="left").pack(anchor="w", pady=(10, 0))

        return frame
