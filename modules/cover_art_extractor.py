"""
PulseBox Module: Cover Art Extractor
Scans the music library and extracts embedded album art to cover.jpg
in each album directory. Runs automatically on startup (only processes
albums missing a cover) and can be triggered manually from Settings.
"""

import os
import threading
from mutagen import File
from mutagen.id3 import ID3
from mutagen.flac import FLAC

from core.config import JukeboxModule, APP_VERSION

SUPPORTED_EXTENSIONS = (".mp3", ".flac", ".m4a", ".aac", ".ogg")


def extract_cover(audio_path: str, output_path: str) -> bool:
    """
    Extract embedded cover art from an audio file and save to output_path.
    Returns True if a cover was saved.
    """
    try:
        ext = audio_path.lower()

        # MP3 — ID3 APIC frame
        if ext.endswith(".mp3"):
            tags = ID3(audio_path)
            for tag in tags.values():
                if tag.FrameID == "APIC":
                    with open(output_path, "wb") as img:
                        img.write(tag.data)
                    return True

        # FLAC — picture block
        elif ext.endswith(".flac"):
            audio = FLAC(audio_path)
            if audio.pictures:
                with open(output_path, "wb") as img:
                    img.write(audio.pictures[0].data)
                return True

        # M4A / AAC — covr atom
        elif ext.endswith((".m4a", ".aac")):
            audio = File(audio_path)
            if audio and "covr" in audio:
                cover = audio["covr"][0]
                with open(output_path, "wb") as img:
                    img.write(bytes(cover))
                return True

        # OGG — METADATA_BLOCK_PICTURE (base64 encoded)
        elif ext.endswith(".ogg"):
            import base64
            import struct
            audio = File(audio_path)
            if audio and "metadata_block_picture" in audio:
                data = base64.b64decode(audio["metadata_block_picture"][0])
                # Skip the picture block header to reach raw image bytes
                offset = 4  # picture type
                mime_len = struct.unpack(">I", data[offset:offset + 4])[0]
                offset += 4 + mime_len
                desc_len = struct.unpack(">I", data[offset:offset + 4])[0]
                offset += 4 + desc_len + 16  # skip desc + 4 ints (width/height/depth/colors)
                img_len = struct.unpack(">I", data[offset:offset + 4])[0]
                offset += 4
                with open(output_path, "wb") as img:
                    img.write(data[offset:offset + img_len])
                return True

    except Exception as e:
        print(f"[CoverArt] Error processing {os.path.basename(audio_path)}: {e}")
    return False


def process_album_dir(album_path: str, force: bool = False) -> str:
    """
    Extract cover art for one album directory.
    Returns: 'skipped' | 'saved' | 'not_found' | 'error'
    """
    cover_path = os.path.join(album_path, "cover.jpg")
    if os.path.exists(cover_path) and not force:
        return "skipped"

    try:
        for filename in sorted(os.listdir(album_path)):
            if filename.lower().endswith(SUPPORTED_EXTENSIONS):
                song_path = os.path.join(album_path, filename)
                if extract_cover(song_path, cover_path):
                    return "saved"
                # Try next track if first had no art
        return "not_found"
    except Exception as e:
        print(f"[CoverArt] Error in {album_path}: {e}")
        return "error"


def scan_and_extract(music_root: str, force: bool = False,
                     progress_cb=None) -> dict:
    """
    Walk the music directory tree and extract cover art for every album.
    Expects structure:  music_root / artist / album / tracks
    Also handles flat:  music_root / album / tracks

    Returns stats dict: saved, skipped, not_found, errors
    """
    stats = {"saved": 0, "skipped": 0, "not_found": 0, "errors": 0}
    album_dirs = []

    for root, dirs, files in os.walk(music_root):
        # If this directory contains audio files, treat it as an album dir
        if any(f.lower().endswith(SUPPORTED_EXTENSIONS) for f in files):
            album_dirs.append(root)

    total = len(album_dirs)
    for i, album_path in enumerate(album_dirs):
        if progress_cb:
            progress_cb(i + 1, total, album_path)
        result = process_album_dir(album_path, force=force)
        stats[result if result in stats else "errors"] += 1

    return stats


# ---------------------------------------------------------------------------
# PulseBox Module wrapper
# ---------------------------------------------------------------------------

class CoverArtExtractor(JukeboxModule):
    """
    Automatically extracts embedded cover art to cover.jpg on startup,
    and exposes a manual trigger + progress panel in the Modules tab.
    """

    MODULE_ID          = "cover_art_extractor"
    MODULE_NAME        = "Cover Art Extractor"
    MODULE_VERSION = APP_VERSION
    MODULE_DESCRIPTION = ("Extracts embedded album art from audio files and saves "
                          "cover.jpg to each album directory for display in the jukebox.")
    MODULE_AUTHOR      = "Andrew J. Morgan"

    def __init__(self):
        super().__init__()
        self._running      = False
        self._progress_var = None
        self._status_var   = None
        self._btn          = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_load(self, app):
        super().on_load(app)
        # Run automatically in background on startup (skip existing covers)
        threading.Thread(target=self._auto_run, daemon=True).start()

    def _auto_run(self):
        music_dir = self.config.get("music_dir")
        if not music_dir or not os.path.isdir(music_dir):
            return
        print(f"[CoverArt] Auto-scan starting: {music_dir}")
        stats = scan_and_extract(music_dir, force=False)
        print(f"[CoverArt] Auto-scan done — "
              f"saved:{stats['saved']}  skipped:{stats['skipped']}  "
              f"not_found:{stats['not_found']}  errors:{stats['errors']}")
        if stats["saved"] > 0:
            self._refresh_ui_artwork()
            self.emit_notification(
                f"Cover art stored for {stats['saved']} album(s)", "success")

    # ------------------------------------------------------------------
    # Artwork refresh
    # ------------------------------------------------------------------

    def _refresh_ui_artwork(self):
        """Refresh library artwork index then redraw the album grid.

        Runs on the main thread: the library's SQLite connection was created
        there and can only be used there, and the album grid is Tkinter. This
        method may be called from a background scan thread, so it marshals the
        work onto the main loop via root.after().
        """
        root = getattr(self.app, "root", None) if self.app else None
        if root is not None:
            root.after(0, self._do_refresh_ui_artwork)
        else:
            self._do_refresh_ui_artwork()

    def _do_refresh_ui_artwork(self):
        try:
            self.library.refresh_artwork()
            if self.app and hasattr(self.app, '_albums_page'):
                self.app._albums_page.refresh()
        except Exception as e:
            print(f"[CoverArt] UI refresh error: {e}")

    # ------------------------------------------------------------------
    # UI Panel
    # ------------------------------------------------------------------

    def get_ui_panel(self, parent):
        import tkinter as tk
        from ui.theme import (BG_PANEL, BG_CARD, BG_DARK, ACCENT, NEON_BLUE,
                              SUCCESS, DANGER, TEXT_PRIMARY, TEXT_SECONDARY,
                              TEXT_DIM, FONT_BODY, FONT_SMALL, FONT_TINY)

        frame = tk.Frame(parent, bg=BG_PANEL)

        self._status_var   = tk.StringVar(value="Ready")
        self._progress_var = tk.StringVar(value="")

        # Status row
        row1 = tk.Frame(frame, bg=BG_PANEL)
        row1.pack(fill="x", pady=(0, 4))
        tk.Label(row1, text="Status:", font=FONT_SMALL,
                 fg=TEXT_PRIMARY, bg=BG_PANEL).pack(side="left")
        tk.Label(row1, textvariable=self._status_var,
                 font=FONT_SMALL, fg=ACCENT, bg=BG_PANEL).pack(side="left", padx=6)

        # Progress label
        tk.Label(frame, textvariable=self._progress_var,
                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_PANEL,
                 anchor="w").pack(fill="x", pady=(0, 6))

        # Buttons
        btn_row = tk.Frame(frame, bg=BG_PANEL)
        btn_row.pack(fill="x")

        self._btn = tk.Button(
            btn_row, text="Extract Missing Covers",
            font=FONT_SMALL, fg=BG_PANEL, bg=ACCENT,
            relief="flat", bd=0, cursor="hand2", padx=10, pady=5,
            command=lambda: self._run_manual(force=False))
        self._btn.pack(side="left", padx=(0, 8))

        tk.Button(
            btn_row, text="Re-extract All (force)",
            font=FONT_SMALL, fg=TEXT_PRIMARY, bg=BG_CARD,
            relief="flat", bd=0, cursor="hand2", padx=10, pady=5,
            command=lambda: self._run_manual(force=True)).pack(side="left")

        return frame

    def _run_manual(self, force: bool = False):
        if self._running:
            return
        self._running = True
        if self._btn:
            self._btn.config(state="disabled")

        music_dir = self.config.get("music_dir")

        def _progress(done, total, path):
            name = os.path.basename(path)
            if self._progress_var:
                self._progress_var.set(f"[{done}/{total}] {name}")
            if self._status_var:
                self._status_var.set("Running…")

        def _run():
            if not music_dir or not os.path.isdir(music_dir):
                if self._status_var:
                    self._status_var.set("Music directory not found")
                self._running = False
                return

            stats = scan_and_extract(music_dir, force=force,
                                     progress_cb=_progress)
            msg = (f"Done — saved:{stats['saved']}  "
                   f"skipped:{stats['skipped']}  "
                   f"not found:{stats['not_found']}")
            if self._status_var:
                self._status_var.set(msg)
            if self._progress_var:
                self._progress_var.set("")
            if self._btn:
                self._btn.config(state="normal")
            self._running = False
            self.emit_notification(
                f"Cover art: {stats['saved']} saved, "
                f"{stats['not_found']} not found", "info")

            # Refresh library artwork index and redraw album grid
            if stats['saved'] > 0:
                self._refresh_ui_artwork()

        threading.Thread(target=_run, daemon=True).start()
