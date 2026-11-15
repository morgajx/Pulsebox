"""
PulseBox Module: Spotify Connect (Official Web API)
=====================================================
Controls Spotify playback via the official Spotify Web API using OAuth
PKCE authentication. Fully within Spotify's Terms of Service.

FEATURES:
  - Browse your Spotify playlists, saved albums and top tracks
  - Search Spotify for artists, albums and tracks
  - Control playback: play, pause, skip, previous, volume, seek
  - See now-playing with album art on the PulseBox player bar
  - Transfer playback to/from any Spotify Connect device
  - Add tracks to the PulseBox local queue (if track is also in library)

REQUIREMENTS:
  - Spotify Premium account (playback control requires Premium)
  - Free Spotify Developer account: https://developer.spotify.com/dashboard
  - Create an app, add redirect URI: http://localhost:8765/callback
  - Copy Client ID into module settings

INSTALL:
  pip install spotipy --break-system-packages

HOW IT WORKS:
  Uses PKCE OAuth flow — no client secret needed. The first time you
  click Authorise, a local HTTP server opens briefly on port 8765 to
  catch the OAuth callback. After that the token is cached and refreshed
  automatically.
"""

import json
import os
import threading
import time
import tkinter as tk
import urllib.request
import urllib.parse
from io import BytesIO
from tkinter import ttk, messagebox

from core.config import JukeboxModule, APP_VERSION

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TOKEN_CACHE   = os.path.expanduser("~/.pulsebox_spotify_token")
REDIRECT_URI  = "http://localhost:8765/callback"
SCOPES        = (
    "user-read-playback-state "
    "user-modify-playback-state "
    "user-read-currently-playing "
    "playlist-read-private "
    "playlist-read-collaborative "
    "user-library-read "
    "user-top-read "
    "user-read-recently-played"
)
POLL_INTERVAL = 5   # seconds between now-playing polls
ART_SIZE      = 80  # px for track art in the panel


# ---------------------------------------------------------------------------
# spotipy availability check
# ---------------------------------------------------------------------------

def _spotipy_available() -> bool:
    try:
        import spotipy  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------

class SpotifyConnect(JukeboxModule):
    MODULE_ID          = "spotify_connect"
    MODULE_NAME        = "Spotify Connect"
    MODULE_VERSION = APP_VERSION
    MODULE_DESCRIPTION = (
        "Control Spotify playback via the official Web API (OAuth PKCE). "
        "Browse playlists, search, and control playback. "
        "Requires Spotify Premium and a free Developer account."
    )
    MODULE_AUTHOR      = "Andrew J. Morgan"

    def __init__(self):
        super().__init__()
        self._sp            = None    # spotipy.Spotify instance
        self._auth_manager  = None
        self._poll_thread   = None
        self._poll_active   = False
        self._current_track = {}
        self._devices       = []

        # UI widgets
        self._status_var    = None
        self._track_var     = None
        self._artist_var    = None
        self._album_var     = None
        self._progress_var  = None
        self._vol_var       = None
        self._device_var    = None
        self._device_combo  = None
        self._art_label     = None
        self._art_photo     = None
        self._browse_frame  = None
        self._results_frame = None
        self._search_var    = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_load(self, app):
        super().on_load(app)
        if _spotipy_available():
            threading.Thread(target=self._try_reconnect, daemon=True).start()

    def on_unload(self):
        self._poll_active = False
        super().on_unload()

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _try_reconnect(self):
        """Attempt silent reconnect using cached token."""
        client_id = self.config.get("spotify_client_id", "")
        if not client_id:
            return
        try:
            import spotipy
            from spotipy.oauth2 import SpotifyPKCE
            auth = SpotifyPKCE(
                client_id=client_id,
                redirect_uri=REDIRECT_URI,
                scope=SCOPES,
                cache_path=TOKEN_CACHE,
                open_browser=False,
            )
            # Only succeed if we already have a valid cached token
            if auth.get_cached_token():
                self._auth_manager = auth
                self._sp = spotipy.Spotify(auth_manager=auth)
                self._set_status("Connected to Spotify")
                self._start_poll()
        except Exception as e:
            print(f"[SpotifyConnect] Silent reconnect failed: {e}")

    def _authorise(self):
        """Full OAuth PKCE flow — opens browser, catches callback."""
        client_id = self.config.get("spotify_client_id", "")
        if not client_id:
            self._set_status("Enter your Spotify Client ID first", error=True)
            return
        if not _spotipy_available():
            self._set_status(
                "spotipy not installed. Run: pip install spotipy --break-system-packages",
                error=True)
            return

        def _auth_thread():
            try:
                import spotipy
                from spotipy.oauth2 import SpotifyPKCE
                auth = SpotifyPKCE(
                    client_id=client_id,
                    redirect_uri=REDIRECT_URI,
                    scope=SCOPES,
                    cache_path=TOKEN_CACHE,
                    open_browser=True,
                )
                self._set_status("Opening browser for Spotify authorisation…")
                # get_access_token starts the local callback server and
                # blocks until the user authorises in the browser
                auth.get_access_token(as_dict=False)
                self._auth_manager = auth
                self._sp = spotipy.Spotify(auth_manager=auth)
                self._set_status("Authorised — connected to Spotify ✓")
                self._start_poll()
                if self.app:
                    self.app.root.after(0, self._refresh_devices)
            except Exception as e:
                self._set_status(f"Authorisation failed: {e}", error=True)

        threading.Thread(target=_auth_thread, daemon=True).start()

    def _disconnect(self):
        self._poll_active = False
        self._sp = None
        self._auth_manager = None
        try:
            os.remove(TOKEN_CACHE)
        except FileNotFoundError:
            pass
        self._set_status("Disconnected")

    # ------------------------------------------------------------------
    # Polling — now playing
    # ------------------------------------------------------------------

    def _start_poll(self):
        if self._poll_active:
            return
        self._poll_active = True
        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def _poll_loop(self):
        while self._poll_active and self._sp:
            try:
                pb = self._sp.current_playback()
                if pb and pb.get("item"):
                    self._on_playback(pb)
            except Exception as e:
                print(f"[SpotifyConnect] Poll error: {e}")
                time.sleep(15)
                continue
            time.sleep(POLL_INTERVAL)

    def _on_playback(self, pb: dict):
        item    = pb.get("item", {})
        artists = ", ".join(a["name"] for a in item.get("artists", []))
        album   = item.get("album", {}).get("name", "")
        title   = item.get("name", "")
        dur_ms  = item.get("duration_ms", 0)
        prog_ms = pb.get("progress_ms", 0)
        vol     = pb.get("device", {}).get("volume_percent", 100)
        is_play = pb.get("is_playing", False)

        art_url = ""
        images  = item.get("album", {}).get("images", [])
        if images:
            art_url = images[-1]["url"]   # smallest thumbnail

        pct = int((prog_ms / dur_ms) * 100) if dur_ms else 0

        self._current_track = {
            "title": title, "artist": artists, "album": album,
            "art_url": art_url, "pct": pct, "vol": vol,
            "is_playing": is_play,
            "uri": item.get("uri", ""),
        }

        if self.app:
            self.app.root.after(0, self._update_now_playing_ui)

    # ------------------------------------------------------------------
    # Playback controls
    # ------------------------------------------------------------------

    def _play_pause(self):
        if not self._sp:
            return
        def _t():
            try:
                pb = self._sp.current_playback()
                if pb and pb.get("is_playing"):
                    self._sp.pause_playback()
                else:
                    self._sp.start_playback()
            except Exception as e:
                self._set_status(f"Play/pause error: {e}", error=True)
        threading.Thread(target=_t, daemon=True).start()

    def _next(self):
        if not self._sp:
            return
        threading.Thread(
            target=lambda: self._sp.next_track(), daemon=True).start()

    def _prev(self):
        if not self._sp:
            return
        threading.Thread(
            target=lambda: self._sp.previous_track(), daemon=True).start()

    def _set_volume(self, val):
        if not self._sp:
            return
        threading.Thread(
            target=lambda: self._sp.volume(int(float(val))),
            daemon=True).start()

    def _refresh_devices(self):
        if not self._sp:
            return
        def _t():
            try:
                result = self._sp.devices()
                self._devices = result.get("devices", [])
                if self.app:
                    self.app.root.after(0, self._populate_devices)
            except Exception as e:
                print(f"[SpotifyConnect] Devices error: {e}")
        threading.Thread(target=_t, daemon=True).start()

    def _populate_devices(self):
        if not self._device_combo:
            return
        names = [f"{d['name']} ({d['type']})" for d in self._devices]
        self._device_combo["values"] = names or ["No devices found"]
        # Select active device
        for i, d in enumerate(self._devices):
            if d.get("is_active"):
                self._device_var.set(names[i])
                break

    def _transfer_playback(self):
        if not self._sp or not self._devices:
            return
        sel = self._device_var.get()
        for d in self._devices:
            label = f"{d['name']} ({d['type']})"
            if label == sel:
                threading.Thread(
                    target=lambda did=d["id"]: self._sp.transfer_playback(did),
                    daemon=True).start()
                self._set_status(f"Transferred to {d['name']}")
                break

    # ------------------------------------------------------------------
    # Browse / Search
    # ------------------------------------------------------------------

    def _search(self):
        if not self._sp:
            self._set_status("Not connected to Spotify", error=True)
            return
        q = self._search_var.get().strip() if self._search_var else ""
        if not q:
            return
        def _t():
            try:
                results = self._sp.search(
                    q, limit=20, type="track,album,artist")
                if self.app:
                    self.app.root.after(
                        0, lambda r=results: self._show_search_results(r))
            except Exception as e:
                self._set_status(f"Search error: {e}", error=True)
        threading.Thread(target=_t, daemon=True).start()

    def _load_playlists(self):
        if not self._sp:
            return
        def _t():
            try:
                result = self._sp.current_user_playlists(limit=50)
                items  = result.get("items", [])
                if self.app:
                    self.app.root.after(
                        0, lambda it=items: self._show_playlists(it))
            except Exception as e:
                self._set_status(f"Playlist error: {e}", error=True)
        threading.Thread(target=_t, daemon=True).start()

    def _load_saved_albums(self):
        if not self._sp:
            return
        def _t():
            try:
                result = self._sp.current_user_saved_albums(limit=50)
                items  = [i["album"] for i in result.get("items", [])]
                if self.app:
                    self.app.root.after(
                        0, lambda it=items: self._show_album_list(it))
            except Exception as e:
                self._set_status(f"Saved albums error: {e}", error=True)
        threading.Thread(target=_t, daemon=True).start()

    def _play_uri(self, uri: str, context_uri: str = ""):
        if not self._sp:
            self._set_status("Not connected to Spotify", error=True)
            return
        def _t():
            try:
                if context_uri:
                    self._sp.start_playback(
                        context_uri=context_uri,
                        offset={"uri": uri})
                else:
                    self._sp.start_playback(uris=[uri])
                self._set_status("Playing on Spotify")
            except Exception as e:
                self._set_status(f"Playback error: {e}", error=True)
        threading.Thread(target=_t, daemon=True).start()

    def _play_context(self, context_uri: str):
        if not self._sp:
            return
        def _t():
            try:
                self._sp.start_playback(context_uri=context_uri)
            except Exception as e:
                self._set_status(f"Playback error: {e}", error=True)
        threading.Thread(target=_t, daemon=True).start()

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------

    def _set_status(self, msg: str, error: bool = False):
        print(f"[SpotifyConnect] {'ERROR' if error else 'OK'}: {msg}")
        if self._status_var and self.app:
            self.app.root.after(0, lambda: self._status_var.set(msg))

    def _update_now_playing_ui(self):
        t = self._current_track
        if self._track_var:
            self._track_var.set(t.get("title", ""))
        if self._artist_var:
            self._artist_var.set(t.get("artist", ""))
        if self._album_var:
            self._album_var.set(t.get("album", ""))
        if self._progress_var:
            self._progress_var.set(t.get("pct", 0))
        if self._vol_var:
            self._vol_var.set(t.get("vol", 100))
        # Load art in background
        art_url = t.get("art_url", "")
        if art_url and self._art_label:
            threading.Thread(
                target=self._load_art, args=(art_url,), daemon=True).start()

    def _load_art(self, url: str):
        try:
            from PIL import Image, ImageTk
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = resp.read()
            img = Image.open(BytesIO(data)).resize(
                (ART_SIZE, ART_SIZE), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            if self.app and self._art_label:
                def _set(p=photo):
                    self._art_photo = p
                    self._art_label.config(image=p)
                self.app.root.after(0, _set)
        except Exception:
            pass

    def _clear_results(self):
        if self._results_frame:
            for w in self._results_frame.winfo_children():
                w.destroy()

    # ------------------------------------------------------------------
    # Results renderers
    # ------------------------------------------------------------------

    def _show_search_results(self, results: dict):
        from ui.theme import (BG_CARD, BG_DARK, ACCENT, TEXT_PRIMARY,
                               TEXT_SECONDARY, FONT_BODY, FONT_SMALL,
                               FONT_TINY, PAD_SM)
        self._clear_results()
        f = self._results_frame

        tracks  = results.get("tracks",  {}).get("items", [])
        albums  = results.get("albums",  {}).get("items", [])
        artists = results.get("artists", {}).get("items", [])

        def _section(title):
            tk.Label(f, text=title, font=FONT_SMALL,
                     fg=ACCENT, bg=BG_DARK, anchor="w").pack(
                fill="x", pady=(6, 2))

        def _track_row(t):
            row = tk.Frame(f, bg=BG_CARD, pady=4)
            row.pack(fill="x", padx=2, pady=1)
            uri     = t.get("uri", "")
            art_uri = t.get("album", {}).get("uri", "")
            name    = t.get("name", "")
            artist  = ", ".join(a["name"] for a in t.get("artists", []))
            album   = t.get("album", {}).get("name", "")
            tk.Label(row, text="▶", font=FONT_SMALL,
                     fg=ACCENT, bg=BG_CARD, cursor="hand2",
                     padx=6).pack(side="left")
            info = tk.Frame(row, bg=BG_CARD)
            info.pack(side="left", fill="x", expand=True)
            tk.Label(info, text=name, font=FONT_BODY,
                     fg=TEXT_PRIMARY, bg=BG_CARD,
                     anchor="w").pack(fill="x")
            tk.Label(info, text=f"{artist}  ·  {album}",
                     font=FONT_TINY, fg=TEXT_PRIMARY,
                     bg=BG_CARD, anchor="w").pack(fill="x")
            row.bind("<Button-1>",
                     lambda e, u=uri: self._play_uri(u))
            for w in row.winfo_children():
                w.bind("<Button-1>",
                       lambda e, u=uri: self._play_uri(u))

        def _album_row(a):
            row = tk.Frame(f, bg=BG_CARD, pady=4)
            row.pack(fill="x", padx=2, pady=1)
            uri    = a.get("uri", "")
            name   = a.get("name", "")
            artist = ", ".join(
                ar["name"] for ar in a.get("artists", []))
            year   = a.get("release_date", "")[:4]
            tk.Label(row, text="💿", font=FONT_SMALL,
                     bg=BG_CARD, padx=6).pack(side="left")
            info = tk.Frame(row, bg=BG_CARD)
            info.pack(side="left", fill="x", expand=True)
            tk.Label(info, text=name, font=FONT_BODY,
                     fg=TEXT_PRIMARY, bg=BG_CARD,
                     anchor="w").pack(fill="x")
            tk.Label(info, text=f"{artist}  ·  {year}",
                     font=FONT_TINY, fg=TEXT_PRIMARY,
                     bg=BG_CARD, anchor="w").pack(fill="x")
            row.bind("<Button-1>",
                     lambda e, u=uri: self._play_context(u))
            for w in row.winfo_children():
                w.bind("<Button-1>",
                       lambda e, u=uri: self._play_context(u))

        if tracks:
            _section(f"Tracks ({len(tracks)})")
            for t in tracks[:10]:
                _track_row(t)

        if albums:
            _section(f"Albums ({len(albums)})")
            for a in albums[:8]:
                _album_row(a)

        if not tracks and not albums and not artists:
            tk.Label(f, text="No results found",
                     font=FONT_SMALL, fg=TEXT_PRIMARY,
                     bg=BG_DARK).pack(pady=20)

    def _show_playlists(self, items: list):
        from ui.theme import (BG_CARD, BG_DARK, ACCENT, TEXT_PRIMARY,
                               TEXT_SECONDARY, FONT_BODY, FONT_SMALL,
                               FONT_TINY)
        self._clear_results()
        f = self._results_frame

        tk.Label(f, text=f"Your Playlists ({len(items)})",
                 font=FONT_SMALL, fg=ACCENT,
                 bg=BG_DARK, anchor="w").pack(fill="x", pady=(4, 6))

        for pl in items:
            uri   = pl.get("uri", "")
            name  = pl.get("name", "")
            owner = pl.get("owner", {}).get("display_name", "")
            total = pl.get("tracks", {}).get("total", 0)
            row   = tk.Frame(f, bg=BG_CARD, pady=4)
            row.pack(fill="x", padx=2, pady=1)
            tk.Label(row, text="▶", font=FONT_SMALL,
                     fg=ACCENT, bg=BG_CARD, cursor="hand2",
                     padx=6).pack(side="left")
            info = tk.Frame(row, bg=BG_CARD)
            info.pack(side="left", fill="x", expand=True)
            tk.Label(info, text=name, font=FONT_BODY,
                     fg=TEXT_PRIMARY, bg=BG_CARD,
                     anchor="w").pack(fill="x")
            tk.Label(info,
                     text=f"by {owner}  ·  {total} tracks",
                     font=FONT_TINY, fg=TEXT_PRIMARY,
                     bg=BG_CARD, anchor="w").pack(fill="x")
            for w in [row] + list(row.winfo_children()) + \
                     list(info.winfo_children()):
                w.bind("<Button-1>",
                       lambda e, u=uri: self._play_context(u))

    def _show_album_list(self, items: list):
        from ui.theme import (BG_CARD, BG_DARK, ACCENT, TEXT_PRIMARY,
                               TEXT_SECONDARY, FONT_BODY, FONT_SMALL,
                               FONT_TINY)
        self._clear_results()
        f = self._results_frame

        tk.Label(f, text=f"Saved Albums ({len(items)})",
                 font=FONT_SMALL, fg=ACCENT,
                 bg=BG_DARK, anchor="w").pack(fill="x", pady=(4, 6))

        for a in items:
            uri    = a.get("uri", "")
            name   = a.get("name", "")
            artist = ", ".join(
                ar["name"] for ar in a.get("artists", []))
            year   = a.get("release_date", "")[:4]
            row    = tk.Frame(f, bg=BG_CARD, pady=4)
            row.pack(fill="x", padx=2, pady=1)
            tk.Label(row, text="💿", font=FONT_SMALL,
                     bg=BG_CARD, padx=6).pack(side="left")
            info = tk.Frame(row, bg=BG_CARD)
            info.pack(side="left", fill="x", expand=True)
            tk.Label(info, text=name, font=FONT_BODY,
                     fg=TEXT_PRIMARY, bg=BG_CARD,
                     anchor="w").pack(fill="x")
            tk.Label(info, text=f"{artist}  ·  {year}",
                     font=FONT_TINY, fg=TEXT_PRIMARY,
                     bg=BG_CARD, anchor="w").pack(fill="x")
            for w in [row] + list(row.winfo_children()) + \
                     list(info.winfo_children()):
                w.bind("<Button-1>",
                       lambda e, u=uri: self._play_context(u))

    # ------------------------------------------------------------------
    # UI Panel
    # ------------------------------------------------------------------

    def get_ui_panel(self, parent) -> tk.Frame:
        from ui.theme import (BG_PANEL, BG_CARD, BG_DARK, ACCENT, ACCENT2,
                               NEON_BLUE, SUCCESS, DANGER, WARNING,
                               TEXT_PRIMARY, TEXT_SECONDARY, TEXT_DIM,
                               FONT_BODY, FONT_SMALL, FONT_TINY,
                               FONT_SUBHEAD, PAD, PAD_SM, BORDER)
        from ui.widgets import ScrollableFrame

        outer = tk.Frame(parent, bg=BG_PANEL)

        # ── Install warning ───────────────────────────────────────────
        if not _spotipy_available():
            tk.Label(outer,
                     text="spotipy not installed",
                     font=FONT_SUBHEAD, fg=DANGER, bg=BG_PANEL).pack(pady=8)
            tk.Label(outer,
                     text="Run in terminal:",
                     font=FONT_BODY, fg=TEXT_PRIMARY, bg=BG_PANEL).pack()
            tk.Label(outer,
                     text="pip install spotipy --break-system-packages",
                     font=("Courier", 10), fg=NEON_BLUE,
                     bg=BG_DARK, padx=8, pady=4).pack(
                fill="x", padx=PAD, pady=4)
            return outer

        # ── Status bar ────────────────────────────────────────────────
        self._status_var = tk.StringVar(
            value="Not connected — enter Client ID and click Authorise")
        tk.Label(outer, textvariable=self._status_var,
                 font=FONT_TINY, fg=NEON_BLUE,
                 bg=BG_PANEL, anchor="w").pack(
            fill="x", padx=PAD, pady=(4, 0))

        # ── Settings row ──────────────────────────────────────────────
        settings = tk.Frame(outer, bg=BG_PANEL)
        settings.pack(fill="x", padx=PAD, pady=4)

        tk.Label(settings, text="Client ID:",
                 font=FONT_BODY, fg=TEXT_PRIMARY,
                 bg=BG_PANEL, width=10, anchor="w").pack(side="left")

        cid_var = tk.StringVar(
            value=self.config.get("spotify_client_id", ""))
        cid_entry = tk.Entry(settings, textvariable=cid_var,
                             font=FONT_BODY, bg=BG_CARD,
                             fg=TEXT_PRIMARY,
                             insertbackground=ACCENT,
                             relief="flat", width=36,
                             highlightthickness=1,
                             highlightcolor=ACCENT,
                             highlightbackground=BORDER)
        cid_entry.pack(side="left", padx=(0, 8), ipady=3)

        def _save_cid():
            cid = cid_var.get().strip()
            self.config.set("spotify_client_id", cid)
            self._set_status(f"Client ID saved: {cid[:8]}…")

        tk.Button(settings, text="Save",
                  font=FONT_SMALL, fg=BG_PANEL, bg=ACCENT,
                  relief="flat", bd=0, padx=10, pady=4,
                  cursor="hand2",
                  command=_save_cid).pack(side="left", padx=(0, 6))

        tk.Button(settings, text="Authorise",
                  font=FONT_SMALL, fg=BG_PANEL, bg=SUCCESS,
                  relief="flat", bd=0, padx=10, pady=4,
                  cursor="hand2",
                  command=self._authorise).pack(side="left", padx=(0, 6))

        tk.Button(settings, text="Disconnect",
                  font=FONT_SMALL, fg=DANGER, bg=BG_CARD,
                  relief="flat", bd=0, padx=10, pady=4,
                  cursor="hand2",
                  command=self._disconnect).pack(side="left")

        tk.Label(settings,
                 text="  Get Client ID: developer.spotify.com/dashboard",
                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_PANEL).pack(
            side="left", padx=8)

        # ── Now Playing ───────────────────────────────────────────────
        np_frame = tk.Frame(outer, bg=BG_CARD,
                            highlightthickness=1,
                            highlightbackground=BORDER)
        np_frame.pack(fill="x", padx=PAD, pady=(0, 4))

        # Art thumbnail
        self._art_label = tk.Label(np_frame, text="♪",
                                   font=("Helvetica", 28),
                                   fg=TEXT_PRIMARY, bg=BG_CARD,
                                   width=ART_SIZE, height=ART_SIZE)
        self._art_label.pack(side="left", padx=8, pady=8)

        np_info = tk.Frame(np_frame, bg=BG_CARD)
        np_info.pack(side="left", fill="both", expand=True, pady=8)

        self._track_var  = tk.StringVar(value="No track playing")
        self._artist_var = tk.StringVar(value="")
        self._album_var  = tk.StringVar(value="")

        tk.Label(np_info, textvariable=self._track_var,
                 font=FONT_SUBHEAD, fg=TEXT_PRIMARY,
                 bg=BG_CARD, anchor="w").pack(fill="x")
        tk.Label(np_info, textvariable=self._artist_var,
                 font=FONT_BODY, fg=ACCENT,
                 bg=BG_CARD, anchor="w").pack(fill="x")
        tk.Label(np_info, textvariable=self._album_var,
                 font=FONT_SMALL, fg=TEXT_PRIMARY,
                 bg=BG_CARD, anchor="w").pack(fill="x")

        # Progress bar
        self._progress_var = tk.IntVar(value=0)
        prog_track = tk.Frame(np_info, bg=BORDER, height=3)
        prog_track.pack(fill="x", pady=(6, 0))
        prog_fill  = tk.Frame(prog_track, bg=ACCENT, height=3)
        prog_fill.place(relx=0, rely=0, relwidth=0, relheight=1)

        def _update_progress_bar(*_):
            pct = self._progress_var.get() / 100
            prog_fill.place(relwidth=max(0, min(1, pct)))
        self._progress_var.trace_add("write", _update_progress_bar)

        # Transport controls
        ctrl = tk.Frame(np_frame, bg=BG_CARD)
        ctrl.pack(side="right", padx=8)

        for txt, cmd in [("⏮", self._prev),
                         ("⏯", self._play_pause),
                         ("⏭", self._next)]:
            tk.Button(ctrl, text=txt,
                      font=("Helvetica", 18),
                      fg=ACCENT, bg=BG_CARD,
                      relief="flat", bd=0,
                      cursor="hand2", padx=6,
                      command=cmd).pack(side="left")

        # Volume
        vol_row = tk.Frame(np_frame, bg=BG_CARD)
        vol_row.pack(side="right", padx=8)
        tk.Label(vol_row, text="Vol", font=FONT_TINY,
                 fg=TEXT_PRIMARY, bg=BG_CARD).pack()
        self._vol_var = tk.IntVar(value=80)
        tk.Scale(vol_row, from_=0, to=100,
                 variable=self._vol_var,
                 orient="vertical", length=80,
                 bg=BG_CARD, fg=ACCENT,
                 troughcolor=BG_DARK,
                 highlightthickness=0, bd=0,
                 showvalue=False,
                 command=self._set_volume).pack()

        # ── Device selector ───────────────────────────────────────────
        dev_row = tk.Frame(outer, bg=BG_PANEL)
        dev_row.pack(fill="x", padx=PAD, pady=2)

        tk.Label(dev_row, text="Device:",
                 font=FONT_BODY, fg=TEXT_PRIMARY,
                 bg=BG_PANEL, width=8, anchor="w").pack(side="left")

        self._device_var   = tk.StringVar(value="")
        self._device_combo = ttk.Combobox(
            dev_row, textvariable=self._device_var,
            state="readonly", font=FONT_BODY, width=30)
        self._device_combo.pack(side="left", padx=(0, 8), ipady=2)

        tk.Button(dev_row, text="⟳",
                  font=FONT_BODY, fg=ACCENT, bg=BG_CARD,
                  relief="flat", bd=0, cursor="hand2", padx=6,
                  command=self._refresh_devices).pack(side="left", padx=(0, 6))

        tk.Button(dev_row, text="Transfer Playback",
                  font=FONT_SMALL, fg=BG_PANEL, bg=NEON_BLUE,
                  relief="flat", bd=0, cursor="hand2",
                  padx=10, pady=4,
                  command=self._transfer_playback).pack(side="left")

        # ── Browse tabs ───────────────────────────────────────────────
        tab_row = tk.Frame(outer, bg=BG_PANEL)
        tab_row.pack(fill="x", padx=PAD, pady=(8, 0))

        for label, cmd in [
            ("Playlists",    self._load_playlists),
            ("Saved Albums", self._load_saved_albums),
        ]:
            tk.Button(tab_row, text=label,
                      font=FONT_SMALL, fg=TEXT_PRIMARY, bg=BG_CARD,
                      relief="flat", bd=0, cursor="hand2",
                      padx=12, pady=6,
                      command=cmd).pack(side="left", padx=(0, 4))

        # ── Search bar ────────────────────────────────────────────────
        search_row = tk.Frame(outer, bg=BG_PANEL)
        search_row.pack(fill="x", padx=PAD, pady=4)

        tk.Label(search_row, text="🔍",
                 font=FONT_BODY, fg=TEXT_PRIMARY,
                 bg=BG_PANEL).pack(side="left")

        self._search_var = tk.StringVar()
        search_ent = tk.Entry(
            search_row, textvariable=self._search_var,
            font=FONT_BODY, bg=BG_CARD, fg=TEXT_PRIMARY,
            insertbackground=ACCENT, relief="flat",
            highlightthickness=1, highlightcolor=ACCENT,
            highlightbackground=BORDER)
        search_ent.pack(side="left", fill="x", expand=True,
                        padx=(6, 6), ipady=4)
        search_ent.bind("<Return>", lambda e: self._search())

        tk.Button(search_row, text="Search",
                  font=FONT_SMALL, fg=BG_PANEL, bg=ACCENT,
                  relief="flat", bd=0, cursor="hand2",
                  padx=12, pady=4,
                  command=self._search).pack(side="left")

        # ── Results area ──────────────────────────────────────────────
        results_outer = tk.Frame(outer, bg=BG_DARK)
        results_outer.pack(fill="both", expand=True,
                           padx=PAD, pady=(4, PAD))

        scroll = ScrollableFrame(results_outer, bg=BG_DARK)
        scroll.pack(fill="both", expand=True)
        self._results_frame = scroll.inner

        # Trigger reconnect UI refresh if already connected
        if self._sp:
            self._refresh_devices()
            if self._current_track:
                self._update_now_playing_ui()
        else:
            self._try_reconnect()

        return outer
