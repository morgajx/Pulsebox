"""
PulseBox Video Page
====================
Displays ripped movies in a cover-art grid (poster.jpg per movie folder),
mirroring the Albums page layout.  Clicking a poster plays the movie
full-screen inside the PulseBox content area using mpv embedded via --wid.

Movie discovery
---------------
Recursively scans <video_output_dir> for .mp4  .mkv  .mov  .m4v  .avi
.ts and .wmv files.  Works with all of these layouts:

  Flat (file directly in video root):
    ~/Videos/The Matrix.mkv

  One-level folder (DVD ripper output):
    ~/Videos/The Matrix (1999)/The Matrix (1999).mp4
    ~/Videos/The Matrix (1999)/poster.jpg

  Nested (makemkv raw output):
    ~/Videos/The Matrix (1999)/title_t00.mkv

  Multiple videos per folder (TV series etc.):
    Each video file becomes its own card.

Poster resolution order (per video):
  1. poster.jpg / poster.png in the same folder as the video
  2. poster.jpg / poster.png in the parent folder
  3. Any *.jpg in the same folder
  4. Placeholder artwork

Each movie dict:
    {
        "title":       str,
        "path":        str,        # absolute path to video file
        "folder":      str,        # parent folder of video file
        "poster":      bytes|None, # JPEG/PNG bytes of poster
        "poster_path": str|None,   # path to poster file if found
    }
"""

import json
import os
import re
import subprocess
import threading
import tkinter as tk
from tkinter import ttk
import json
import re
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
from urllib.error import URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from .theme import (ACCENT, BG_CARD, BG_CARD_HOVER, BG_DARK,
                    BG_PANEL, BORDER, BORDER_ACCENT, DANGER, FONT_BODY,
                    FONT_DISPLAY, FONT_HEADING, FONT_SMALL, FONT_SUBHEAD,
                    FONT_TINY, NEON_BLUE, PAD, PAD_SM, SUCCESS,
                    TEXT_DIM, TEXT_PRIMARY, TEXT_SECONDARY, WARNING,
                    get_scale)
from .widgets import ScrollableFrame

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".m4v", ".avi", ".ts", ".wmv"}

# Poster size scales with screen resolution (matches the album grid, which
# uses the theme's scaled CARD_W). Without this the posters stay 120px wide on
# any screen, so on larger displays a fixed column count can't fill the row.
POSTER_W = max(1, int(120 * get_scale()))
POSTER_H = max(1, int(178 * get_scale()))   # 2:3 movie poster ratio

TMDB_SEARCH = "https://api.themoviedb.org/3/search/movie"
TMDB_IMG    = "https://image.tmdb.org/t/p/w500"


def _fetch_tmdb_poster(title: str, api_key: str) -> bytes | None:
    """
    Search TMDb for `title` and return poster image bytes, or None.
    Strips trailing year like '(1999)' from the title before searching.

    TMDb's search is fuzzy/relevance-ranked, so the top result is not
    necessarily the requested title — it can return a different, more
    popular film with a similar name. We only accept a result whose
    title closely matches the requested title (exact or substring match
    after normalising case/punctuation); otherwise we skip the poster
    rather than show confidently-wrong artwork.
    """
    if not api_key:
        return None
    clean = re.sub(r'\s*\(\d{4}\)\s*$', '', title).strip()
    try:
        url = (f"{TMDB_SEARCH}?api_key={api_key}"
               f"&query={quote_plus(clean)}&language=en-US")
        req  = Request(url, headers={"User-Agent": "PulseBox/1.0"})
        data = json.loads(urlopen(req, timeout=10).read())
        results = data.get("results", [])
        if not results:
            return None

        def _norm(s: str) -> str:
            return re.sub(r'[^a-z0-9]+', '', s.lower())

        wanted = _norm(clean)
        best = None
        for r in results:
            cand = _norm(r.get("title", "") or r.get("original_title", ""))
            if cand == wanted:
                best = r
                break
        if best is None:
            for r in results:
                cand = _norm(r.get("title", "") or r.get("original_title", ""))
                if cand and (wanted in cand or cand in wanted):
                    best = r
                    break
        if best is None:
            print(f"[VideoPage] TMDb: no close title match for '{clean}' "
                  f"(top result was '{results[0].get('title')}') — "
                  f"skipping poster")
            return None

        poster_path = best.get("poster_path", "")
        if not poster_path:
            return None
        return urlopen(
            Request(TMDB_IMG + poster_path,
                    headers={"User-Agent": "PulseBox/1.0"}),
            timeout=15).read()
    except Exception as e:
        print(f"[VideoPage] TMDb fetch failed for '{title}': {e}")
        return None


# Folder names produced by makemkv / rippers that should NOT be used as titles
_GENERIC_FOLDERS = {
    "video_ts", "bdmv", "backup", "certificate", "stream",
    "disc1", "disc2", "disc3",
}


# ---------------------------------------------------------------------------
# Scanner helpers
# ---------------------------------------------------------------------------

def _find_poster(folder: str):
    """Return (path, bytes) for the best poster image in folder, or (None, None)."""
    # Preferred names first
    for name in ("poster.jpg", "poster.jpeg", "poster.png",
                 "cover.jpg",  "cover.jpeg",  "cover.png",
                 "thumb.jpg",  "thumb.jpeg",  "thumb.png"):
        p = os.path.join(folder, name)
        if os.path.isfile(p):
            try:
                return p, open(p, "rb").read()
            except Exception:
                pass
    # Any JPEG/PNG in the folder
    try:
        for f in sorted(os.listdir(folder)):
            if f.lower().endswith((".jpg", ".jpeg", ".png")):
                p = os.path.join(folder, f)
                try:
                    return p, open(p, "rb").read()
                except Exception:
                    pass
    except Exception:
        pass
    return None, None


def _title_from_path(video_path: str, video_root: str = "") -> str:
    """
    Derive a human-readable title from a video file path.

    Handles these layouts:
      ~/Videos/flat_file.mkv                         → "flat file"
      ~/Videos/The Matrix/The Matrix.mkv             → "The Matrix"
      ~/Videos/The Matrix (1999)/film.mp4            → "The Matrix (1999)"
      ~/Videos/Blade Runner/disc1/title_t00.mkv      → "Blade Runner"
    """
    folder    = os.path.dirname(video_path)
    folder_nm = os.path.basename(folder)
    stem      = os.path.splitext(os.path.basename(video_path))[0]
    parent    = os.path.dirname(folder)
    parent_nm = os.path.basename(parent)

    # ── Flat file: video is directly inside the root scan directory ───────────
    # e.g. ~/Videos/film.mkv  — folder IS the video root, use filename stem
    if video_root and os.path.abspath(folder) == os.path.abspath(video_root):
        clean = re.sub(r'[._]', ' ', stem).strip()
        return clean if clean else stem

    # ── Generic intermediate folder (disc1, VIDEO_TS …) ──────────────────────
    if folder_nm.lower() in _GENERIC_FOLDERS:
        if parent_nm and parent_nm.lower() not in _GENERIC_FOLDERS:
            return parent_nm
        return stem

    # ── Generic rip filename (title_t00, track01 …) ───────────────────────────
    _generic_stems = {"title_t00", "title_t01", "title_t02",
                      "track01", "track02", "full_movie", "movie"}
    if stem.lower() in _generic_stems or re.match(r'^title_t\d+$', stem.lower()):
        if folder_nm.lower() not in _GENERIC_FOLDERS:
            return folder_nm

    # ── Single video in a named folder → folder is the title ─────────────────
    # This is the primary case: Videos/Filmname/Filmname.mkv
    if folder_nm:
        try:
            siblings = [f for f in os.listdir(folder)
                        if os.path.splitext(f)[1].lower() in VIDEO_EXTS]
            if len(siblings) == 1:
                return folder_nm
        except Exception:
            pass

    # ── Fallback: clean up the filename stem ──────────────────────────────────
    clean = re.sub(r'[._]', ' ', stem).strip()
    return clean if clean else stem


# ---------------------------------------------------------------------------
# Public scanner
# ---------------------------------------------------------------------------

def scan_video_library(video_dir: str) -> list[dict]:
    """
    Recursively walk video_dir and return one dict per video file,
    sorted alphabetically by title.  Deduplicates by canonical path.
    Handles flat files, one-level folders and deeply nested structures.
    """
    if not os.path.isdir(video_dir):
        return []

    seen:   set[str]   = set()
    movies: list[dict] = []

    for root, dirs, files in os.walk(video_dir, followlinks=True):
        dirs.sort(key=str.lower)
        for fname in sorted(files, key=str.lower):
            ext = os.path.splitext(fname)[1].lower()
            if ext not in VIDEO_EXTS:
                continue

            video_path = os.path.abspath(os.path.join(root, fname))
            if video_path in seen:
                continue
            seen.add(video_path)

            folder = os.path.dirname(video_path)
            title  = _title_from_path(video_path, video_dir)

            # Look for poster: video folder → parent folder → grandparent folder
            # Covers: flat / one-level / two-level (Videos/Filmname/file)
            poster_path, poster_data = _find_poster(folder)
            if poster_path is None:
                p1 = os.path.dirname(folder)
                if p1 != folder and os.path.isdir(p1):
                    poster_path, poster_data = _find_poster(p1)
            if poster_path is None:
                p2 = os.path.dirname(p1) if 'p1' in dir() else ""
                if p2 and p2 != p1 and os.path.isdir(p2):
                    poster_path, poster_data = _find_poster(p2)

            movies.append({
                "title":       title,
                "path":        video_path,
                "folder":      folder,
                "poster":      poster_data,
                "poster_path": poster_path,
            })

    movies.sort(key=lambda m: m["title"].lower())
    return movies


# ---------------------------------------------------------------------------
# Movie card widget
# ---------------------------------------------------------------------------

class MovieCard(tk.Frame):
    """Clickable poster tile — mirrors AlbumCard."""

    def __init__(self, parent, movie: dict, on_click=None, **kwargs):
        super().__init__(parent, bg=BG_CARD, cursor="hand2",
                         padx=4, pady=4, **kwargs)
        self._movie    = movie
        self._on_click = on_click
        self._photo    = None
        self._build()

    def _build(self):
        self._canvas = tk.Canvas(self, width=POSTER_W, height=POSTER_H,
                                 bg=BG_PANEL, highlightthickness=0)
        self._canvas.pack()

        tk.Label(self, text=self._movie["title"],
                 font=FONT_SMALL, fg=TEXT_PRIMARY, bg=BG_CARD,
                 wraplength=POSTER_W + 4, anchor="w",
                 justify="left").pack(fill="x", padx=4, pady=(4, 0))

        tk.Label(self, text="▶ Play",
                 font=FONT_TINY, fg=ACCENT, bg=BG_CARD
                 ).pack(padx=4, pady=(0, 4))

        for w in [self] + list(self.winfo_children()):
            w.bind("<Enter>",    self._hover_on)
            w.bind("<Leave>",    self._hover_off)
            w.bind("<Button-1>", self._click)

        self.after(5, self._load_poster)

    def _load_poster(self):
        from .image_loader import load_image_tk
        data = self._movie.get("poster")
        if data:
            photo = load_image_tk(data, POSTER_W, height=POSTER_H)
            if photo:
                self._photo = photo
                self._canvas.delete("all")
                self._canvas.create_image(
                    POSTER_W // 2, POSTER_H // 2,
                    image=self._photo, anchor="center")
                # Force the canvas to repaint immediately. Without this,
                # newly drawn images on a canvas that hasn't been mapped/
                # exposed yet can remain invisible until some other event
                # (e.g. mouse hover) triggers a redraw.
                self._canvas.update_idletasks()
                # Belt-and-braces: explicitly raise the image item and
                # request a redraw of the canvas's exposed region.
                try:
                    self._canvas.tag_raise("all")
                    self._canvas.event_generate("<Expose>", when="tail")
                except Exception:
                    pass
                return
        # Placeholder
        self._canvas.create_rectangle(0, 0, POSTER_W, POSTER_H,
                                      fill=BG_PANEL, outline=BORDER)
        self._canvas.create_text(POSTER_W // 2, POSTER_H // 2 - 10,
                                 text="🎬", font=("Helvetica", 36),
                                 fill=BORDER_ACCENT)
        self._canvas.create_text(POSTER_W // 2, POSTER_H // 2 + 28,
                                 text="No poster", font=("Helvetica", 9),
                                 fill=TEXT_DIM)
        self._canvas.update_idletasks()

    def _hover_on(self, _e=None):
        self.config(bg=BG_CARD_HOVER)
        for w in self.winfo_children():
            try: w.config(bg=BG_CARD_HOVER)
            except Exception: pass

    def _hover_off(self, _e=None):
        self.config(bg=BG_CARD)
        for w in self.winfo_children():
            try: w.config(bg=BG_CARD)
            except Exception: pass

    def _click(self, _e=None):
        if self._on_click:
            self._on_click(self._movie)


# ---------------------------------------------------------------------------
# Embedded VLC player
# ---------------------------------------------------------------------------
# VLC is used instead of mpv because VLC has full codec support on
# Raspberry Pi OS Trixie out of the box.  cvlc (VLC without its own GUI)
# renders into the Tkinter canvas via the --drawable-xid flag.
# ---------------------------------------------------------------------------

class _MpvPlayer(tk.Frame):
    """
    VLC video player embedded via set_xwindow().
    The main PlayerBar at the bottom is switched to video mode,
    providing title, seek bar, pause/resume and stop controls.
    No separate controls bar drawn here — canvas fills everything.
    """

    _POLL_MS = 500

    def __init__(self, parent, movie: dict,
                 player_bar=None, app=None, on_close=None, **kwargs):
        super().__init__(parent, bg="#000000", **kwargs)
        self._movie      = movie
        self._player_bar = player_bar
        self._app        = app
        self._on_close   = on_close
        self._vlc_inst   = None
        self._vlc_player = None
        self._poll_job   = None
        self._paused     = False
        self._build()

    def _build(self):
        self.config(bg="#000000")
        self._canvas = tk.Canvas(self, bg="#000000", highlightthickness=0)
        self._canvas.pack(fill="both", expand=True)
        self.winfo_toplevel().bind("<Escape>",
                                   lambda _e: self._stop_and_close())
        self.after(400, self._launch_vlc)

    def _launch_vlc(self):
        try:
            import vlc
        except ImportError:
            tk.Label(self, text="python-vlc not found.",
                     font=("Helvetica", 14), fg=DANGER,
                     bg="#000000").pack(expand=True)
            return

        self._canvas.update_idletasks()
        wid = self._canvas.winfo_id()

        self._vlc_inst   = vlc.Instance("--no-osd",
                                        "--no-video-title-show",
                                        "--no-snapshot-preview",
                                        "--avcodec-hw=none")
        self._vlc_player = self._vlc_inst.media_player_new()
        media = self._vlc_inst.media_new(self._movie["path"])
        self._vlc_player.set_media(media)
        self._vlc_player.set_xwindow(wid)
        self._vlc_player.play()

        # Switch PlayerBar to video mode
        if self._player_bar:
            self._player_bar.enter_video_mode(
                title      = self._movie["title"],
                on_pause   = self._toggle_pause,
                on_restart = self._restart,
                on_stop    = self._stop_and_close)
            self._player_bar.set_video_seek_callback(self._seek_to_pct)

        events = self._vlc_player.event_manager()
        events.event_attach(vlc.EventType.MediaPlayerEndReached,
                            lambda _e: self.after(0, self._stop_and_close))

        self.after(self._POLL_MS, self._poll_progress)

    def _poll_progress(self):
        if not self._vlc_player:
            return
        try:
            pos_ms = self._vlc_player.get_time()
            dur_ms = self._vlc_player.get_length()
            if pos_ms >= 0 and dur_ms > 0:
                if self._player_bar:
                    self._player_bar.update_video_position(
                        pos_ms / 1000.0, dur_ms / 1000.0, self._paused)
        except Exception:
            pass
        self._poll_job = self.after(self._POLL_MS, self._poll_progress)

    def _toggle_pause(self):
        if not self._vlc_player:
            return
        self._vlc_player.pause()
        self._paused = not self._paused

    def _restart(self):
        if not self._vlc_player:
            return
        self._vlc_player.set_time(0)
        if self._paused:
            self._vlc_player.pause()
            self._paused = False

    def _seek_to_pct(self, pct: float):
        if not self._vlc_player:
            return
        dur_ms = self._vlc_player.get_length()
        if dur_ms > 0:
            self._vlc_player.set_time(int((pct / 100) * dur_ms))

    def _stop_and_close(self):
        if self._poll_job:
            try: self.after_cancel(self._poll_job)
            except Exception: pass
        try: self.winfo_toplevel().unbind("<Escape>")
        except Exception: pass
        if self._player_bar:
            try: self._player_bar.exit_video_mode()
            except Exception: pass
        if self._vlc_player:
            try:
                self._vlc_player.stop()
                self._vlc_player.release()
            except Exception: pass
            self._vlc_player = None
        if self._vlc_inst:
            try: self._vlc_inst.release()
            except Exception: pass
            self._vlc_inst = None
        if self._on_close:
            self._on_close()


def _fmt(secs: int) -> str:
    """Format seconds as M:SS or H:MM:SS."""
    secs = max(0, int(secs))
    h, rem = divmod(secs, 3600)
    m, s   = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


# ---------------------------------------------------------------------------
# VideoPage
# ---------------------------------------------------------------------------

class VideoPage(tk.Frame):
    """
    Main video library page — poster grid + embedded player.
    Mirrors AlbumsPage structure so it feels native to PulseBox.
    """

    def __init__(self, parent, config=None, app=None, **kwargs):
        super().__init__(parent, bg=BG_DARK, **kwargs)
        self._config  = config
        self._app     = app
        self._movies: list[dict] = []
        self._player_frame: _MpvPlayer | None = None
        self._library_ready = False

        self._build()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build(self):
        hdr = tk.Frame(self, bg=BG_DARK)
        hdr.pack(fill="x", padx=PAD, pady=(PAD, 4))

        tk.Label(hdr, text="VIDEO LIBRARY", font=FONT_DISPLAY,
                 fg=ACCENT, bg=BG_DARK).pack(side="left")

        self._lbl_count = tk.Label(hdr, text="",
                                   font=FONT_SMALL, fg=TEXT_PRIMARY, bg=BG_DARK)
        self._lbl_count.pack(side="right", pady=4, padx=12)

        tk.Button(hdr, text="⟳ Rescan",
                  font=FONT_SMALL, fg=ACCENT, bg=BG_CARD,
                  relief="flat", bd=0, cursor="hand2", padx=10, pady=4,
                  command=self.refresh).pack(side="right", padx=4)

        # ── Video directory display + change button ───────────────────
        dir_row = tk.Frame(self, bg=BG_DARK)
        dir_row.pack(fill="x", padx=PAD, pady=(0, 2))
        tk.Label(dir_row, text="Directory:", font=FONT_TINY,
                 fg=TEXT_PRIMARY, bg=BG_DARK).pack(side="left")
        self._dir_lbl = tk.Label(dir_row, text=self._video_dir(),
                                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_DARK)
        self._dir_lbl.pack(side="left", padx=(4, 0))

        # ── Search bar ────────────────────────────────────────────────
        search_row = tk.Frame(self, bg=BG_DARK)
        search_row.pack(fill="x", padx=PAD, pady=(0, PAD_SM))
        tk.Label(search_row, text="🔍", font=FONT_BODY,
                 fg=TEXT_PRIMARY, bg=BG_DARK).pack(side="left")
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._filter())
        ent = tk.Entry(search_row, textvariable=self._search_var,
                       font=FONT_BODY, bg=BG_PANEL, fg=TEXT_PRIMARY,
                       insertbackground=ACCENT, relief="flat",
                       highlightthickness=1, highlightcolor=ACCENT,
                       highlightbackground=BORDER)
        ent.pack(side="left", fill="x", expand=True, padx=(6, 6), ipady=4)
        tk.Button(search_row, text="✕ Clear",
                  font=FONT_SMALL, fg=TEXT_PRIMARY, bg=BG_CARD,
                  relief="flat", bd=0, cursor="hand2", padx=10, pady=4,
                  command=lambda: self._search_var.set("")).pack(side="left")

        self._content = tk.Frame(self, bg=BG_DARK)
        self._content.pack(fill="both", expand=True)

        self._show_placeholder("Tap ⟳ Rescan or open the Video tab to load library.")

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------

    def refresh(self):
        """Rescan video directory, then fetch missing posters from TMDb."""
        self._library_ready = False
        self._show_placeholder("Scanning video library…")

        def _scan():
            video_dir = self._video_dir()
            movies    = scan_video_library(video_dir)
            self.after(0, lambda: self._on_scan_done(movies))

        threading.Thread(target=_scan, daemon=True, name="video-scan").start()

    def _video_dir(self) -> str:
        if self._config:
            d = self._config.get("dvd_output_dir", "")
            if d:
                return os.path.expanduser(d)
        return os.path.expanduser("~/Videos")

    def _on_scan_done(self, movies: list[dict]):
        self._movies = movies
        self._library_ready = True
        vdir = self._video_dir()
        self._dir_lbl.config(text=vdir)
        count = len(movies)
        self._lbl_count.config(
            text=f"{count} video{'s' if count != 1 else ''}")

        for w in self._content.winfo_children():
            w.destroy()

        if not movies:
            self._show_empty()
        else:
            self._show_grid(movies)

        # Kick off background TMDb poster fetch for any movie missing one
        api_key = ""
        if self._config:
            api_key = self._config.get("tmdb_api_key", "")
        if api_key:
            missing = [m for m in movies if not m.get("poster")]
            if missing:
                threading.Thread(
                    target=self._fetch_missing_posters,
                    args=(missing, api_key),
                    daemon=True,
                    name="tmdb-poster-fetch").start()

    def _fetch_missing_posters(self, movies: list[dict], api_key: str):
        """
        Background thread: fetch TMDb posters for movies that have none.
        Saves poster.jpg next to the video file and refreshes the card.
        """
        for movie in movies:
            title = movie["title"]
            print(f"[VideoPage] Fetching TMDb poster for: {title}")
            data = _fetch_tmdb_poster(title, api_key)
            if data:
                # Save next to the video file as poster.jpg
                save_dir  = movie["folder"]
                save_path = os.path.join(save_dir, "poster.jpg")
                try:
                    with open(save_path, "wb") as f:
                        f.write(data)
                    movie["poster"]      = data
                    movie["poster_path"] = save_path
                    print(f"[VideoPage] Saved poster: {save_path}")
                    # Refresh the card in the UI on the Tk thread
                    self.after(0, self._refresh_card_for, movie)
                except Exception as e:
                    print(f"[VideoPage] Could not save poster for {title}: {e}")
            else:
                print(f"[VideoPage] No TMDb result for: {title}")

    def _refresh_card_for(self, movie: dict):
        """Reload the poster image on the already-displayed card for this movie."""
        # Walk all MovieCard widgets and reload the one matching this path
        for widget in self.winfo_children():
            self._reload_card_recursive(widget, movie)

    def _reload_card_recursive(self, widget, movie: dict):
        if isinstance(widget, MovieCard):
            if widget._movie.get("path") == movie.get("path"):
                widget._load_poster()
                return
        for child in widget.winfo_children():
            self._reload_card_recursive(child, movie)

    # ------------------------------------------------------------------
    # Grid
    # ------------------------------------------------------------------

    def _show_grid(self, movies: list[dict]):
        scroll = ScrollableFrame(self._content, bg=BG_DARK)
        scroll.pack(fill="both", expand=True)

        # Fill the row: derive the column count from the actual viewport width
        # and the (scaled) poster width, so the grid fills whatever screen it's
        # on — the same way the album grid does. A config value, if set, caps
        # the maximum; 0/absent means "as many as fit".
        card_total = POSTER_W + 2 * PAD_SM
        avail = scroll.viewport_width() or self.winfo_width() - 24
        if avail < card_total:
            avail = 1280   # sensible fallback before the widget is mapped
        cols = max(1, avail // card_total)
        cap = self._config.get("video_columns_per_row", 0) if self._config else 0
        if cap and cap > 0:
            cols = min(cols, cap)

        for i, movie in enumerate(movies):
            r, c = divmod(i, cols)
            card = MovieCard(scroll.inner, movie,
                             on_click=self._on_movie_click)
            card.grid(row=r, column=c, padx=PAD_SM, pady=PAD_SM, sticky="nw")

        scroll.bind_all_children_scroll(scroll.inner)
        scroll.refresh_scrollregion()

        # Force a full geometry/paint pass immediately. Without this,
        # newly-created MovieCard canvases inside a freshly-built
        # ScrollableFrame can remain visually blank — their grid()
        # placement and canvas.create_image() calls are queued but not
        # painted — until some unrelated event (e.g. mouse hover)
        # triggers Tk to flush the pending update queue.
        self.update_idletasks()
        scroll.update_idletasks()
        self.winfo_toplevel().update_idletasks()

    def _filter(self):
        if not self._library_ready:
            return
        q = self._search_var.get().strip().lower()
        filtered = ([m for m in self._movies
                     if q in m["title"].lower()] if q else self._movies)
        for w in self._content.winfo_children():
            w.destroy()
        if filtered:
            self._show_grid(filtered)
        else:
            self._show_empty(f'No videos matching "{self._search_var.get()}"')

    def _show_placeholder(self, msg: str):
        for w in self._content.winfo_children():
            w.destroy()
        tk.Label(self._content, text=msg,
                 font=FONT_HEADING, fg=TEXT_PRIMARY, bg=BG_DARK,
                 justify="center").pack(expand=True)

    def _show_empty(self, msg: str = ""):
        if not msg:
            vdir = self._video_dir()
            msg = (f"No videos found in:\n{vdir}\n\n"
                   "Supported formats: MP4  MKV  MOV  M4V  AVI  TS  WMV\n\n"
                   "Use Modules → DVD Ripper to rip a disc,\n"
                   "or copy video files into the directory above.")
        tk.Label(self._content, text=msg,
                 font=FONT_BODY, fg=TEXT_PRIMARY, bg=BG_DARK,
                 justify="center").pack(expand=True, pady=PAD * 4)

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    def _on_movie_click(self, movie: dict):
        if self._app and hasattr(self._app, "player"):
            from core.player import PlayerState
            if self._app.player.get_state() == PlayerState.PLAYING:
                self._app.player.stop()

        # Get PlayerBar reference — video mode reuses it for controls
        pb = None
        if self._app and hasattr(self._app, "_player_bar"):
            pb = self._app._player_bar

        for w in self.winfo_children():
            w.pack_forget()
        player = _MpvPlayer(self, movie,
                            player_bar=pb,
                            app=self._app,
                            on_close=self._on_player_close)
        player.pack(fill="both", expand=True)
        self._player_frame = player

    def _on_player_close(self):
        if self._player_frame:
            self._player_frame.destroy()
            self._player_frame = None
        for w in self.winfo_children():
            if w is self._content:
                w.pack(fill="both", expand=True)
            else:
                w.pack(fill="x")
        for w in self._content.winfo_children():
            w.destroy()
        q = self._search_var.get().strip().lower()
        movies = ([m for m in self._movies if q in m["title"].lower()]
                  if q else self._movies)
        if movies:
            self._show_grid(movies)
        else:
            self._show_empty()

