"""
PulseBox — Jukebox Application  v3.0
Stream. Play. Enjoy.
Author: Andrew J. Morgan
"""

import os
import sys
import threading
import tkinter as tk
from tkinter import messagebox

# ---------------------------------------------------------------------------
# Base-path resolution — works for both plain Python and Nuitka --onefile
# ---------------------------------------------------------------------------
# When compiled with Nuitka --onefile, __file__ points inside a temp
# extraction directory.  The modules/ directory (kept as Python source) lives
# next to the *binary*, not next to __file__.  We resolve the binary's real
# location via sys.argv[0] so dynamic module loading always finds modules/.
# ---------------------------------------------------------------------------
def _app_base_dir() -> str:
    """Return the directory that contains the pulsebox binary (or script).

    Resolution order:
      1. PULSEBOX_DIR env var (set by run_pulsebox.sh launcher — most reliable)
      2. sys.argv[0] when compiled with Nuitka --onefile (sys.frozen=True)
      3. __file__ when running as plain Python script
    """
    # Launcher sets PULSEBOX_DIR to the directory containing the binary
    env_dir = os.environ.get("PULSEBOX_DIR", "")
    if env_dir and os.path.isdir(env_dir):
        return env_dir

    # Nuitka --onefile sets sys.frozen = True; argv[0] is the binary path
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.argv[0]))

    # Plain Python: use __file__
    return os.path.dirname(os.path.abspath(__file__))

_BASE_DIR = _app_base_dir()

# Allow running from the project root
sys.path.insert(0, _BASE_DIR)

from core import JukeboxPlayer, MusicLibrary, ConfigManager, ModuleRegistry  # PulseBox core
from core.playlist import PlaylistManager
from ui.theme import *
from ui.theme import init_scale
from ui.widgets import PlayerBar
from ui.albums_page import AlbumsPage
from ui.settings_page import SettingsPage
from ui.playlist_page import PlaylistPage
from ui.modules_page import ModulesPage  # displayed as Plugins
from ui.about_page import AboutPage
from ui.equalizer_page import EqualizerPage
from ui.radio_page import RadioPage
from ui.splash import SplashScreen
from ui.video_page import VideoPage



def _darken(hex_colour: str, factor: float) -> str:
    """Return a darkened version of a hex colour (factor 0.0-1.0 = brightness)."""
    hex_colour = hex_colour.lstrip("#")
    r, g, b = (int(hex_colour[i:i+2], 16) for i in (0, 2, 4))
    r, g, b = (int(c * factor) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


class PulseBoxApp:
    """
    PulseBox root application.

    Public attributes (available to modules via self.app):
        player   : JukeboxPlayer
        library  : MusicLibrary
        config   : ConfigManager
        modules  : ModuleRegistry
        root     : tk.Tk
    """

    def __init__(self):
        self.config = ConfigManager()
        self.player = JukeboxPlayer()
        self.library = MusicLibrary(
            music_dir=self.config.get("music_dir"),
        )
        self.modules = ModuleRegistry(
            modules_dir=os.path.join(_BASE_DIR, "modules"))
        self.playlist = PlaylistManager(self.library, self.player)

        self.root = tk.Tk()
        # NOTE: Scaling is now detected at ui.theme import time (before any
        # page module's `from .theme import *`), so values are already
        # correct here. init_scale(self.root) is kept as a safe no-op-ish
        # call in case a future refactor needs the real root for scaling.
        init_scale(self.root)
        self._setup_window()
        self._build_ui()
        self._wire_events()
        self._load_library()
        self._load_modules()
        self._apply_kiosk_cursor()   # hide cursor across the freshly-built UI

    # ------------------------------------------------------------------
    # Window setup
    # ------------------------------------------------------------------

    def _setup_window(self):
        self.root.title("PulseBox")

        # Default window size scales with screen resolution.
        # Base design is 1280x800; on larger screens (e.g. 1080p) open
        # proportionally larger so the window isn't tiny on the desktop
        # in non-kiosk mode. Capped at 90% of screen size.
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        win_w = min(int(1280 * get_scale()), int(sw * 0.9))
        win_h = min(int(800  * get_scale()), int(sh * 0.9))
        self.root.geometry(f"{win_w}x{win_h}")

        self.root.minsize(900, 600)
        self.root.configure(bg=BG_DARK)

        # Make it feel like a kiosk if configured
        self._kiosk_hide_cursor = False
        if self.config.get("kiosk_mode"):
            self.root.attributes("-fullscreen", True)
            # Touchscreen kiosk: there's no mouse, so hide the pointer. A
            # parked cursor otherwise sits over the app and touches land as
            # hover-at-the-cursor (pointer -> hand) instead of taps, which
            # feels like a frozen screen. option_add makes every widget built
            # afterwards default to no cursor; _apply_kiosk_cursor() then
            # overrides the explicit hand2 cursors on buttons.
            if self.config.get("hide_cursor", True):
                self.root.option_add("*Cursor", "none")
                self.root.configure(cursor="none")
                self._kiosk_hide_cursor = True
        else:
            self.root.resizable(True, True)

        # Bind Escape to exit fullscreen / quit kiosk
        self.root.bind("<Escape>", self._on_escape)

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        # ── Navigation bar (top — no separate title bar) ──────────────
        nav = tk.Frame(self.root, bg="#000000", height=52)
        nav.pack(fill="x")
        nav.pack_propagate(False)
        self._nav_frame = nav

        # Live status pill — sits in nav bar right side before power buttons
        # Status label packed after nav items so it appears on right
        self._nav_buttons: dict[str, tk.Button] = {}
        self._pages: dict[str, tk.Frame] = {}
        self._active_page: str = ""

        nav_items = [
            ("albums",     "🎵  Music",       "#e8b004"),
            ("radio",      "📻  Radio",       "#ff2d78"),
            ("video",      "🎬  Video",       "#00d4ff"),
            ("equalizer",  "🎚  EQ",          "#00cc88"),
            ("playlist",   "♫  Playlist",     "#ff6b35"),
            ("modules",    "⚙  Plugins",      "#4488ff"),
            ("settings",   "⚙  Settings",     "#aaaaaa"),
        ]

        self._nav_colours: dict[str, str] = {
            pid: col for pid, _, col in nav_items}

        for page_id, label, accent in nav_items:
            wrap = tk.Frame(nav, bg="#000000")
            wrap.pack(side="left")

            btn = tk.Button(wrap, text=label,
                            font=FONT_BODY,
                            fg="#0a0a0f", bg="#4fc3f7",
                            relief="flat", bd=0, cursor="hand2",
                            padx=16, pady=10,
                            activeforeground="#0a0a0f",
                            activebackground="#4fc3f7",
                            command=lambda pid=page_id: self.show_page(pid))
            btn.pack(fill="x")

            # 3px accent colour underline — shown only when active
            bar = tk.Frame(wrap, bg=accent, height=3)
            bar.pack(fill="x")
            bar.pack_forget()

            btn._accent_col  = accent
            btn._inactive_bg = "#4fc3f7"
            btn._wrap        = wrap
            btn._accent_bar  = bar

            self._nav_buttons[page_id] = btn

        # ── Power buttons (right side of nav bar) ─────────────────────
        tk.Button(nav, text="⏻ Shutdown",
                  font=FONT_SMALL, fg="#000000", bg="#ff6b35",
                  relief="flat", bd=0, cursor="hand2",
                  padx=12, pady=10,
                  activeforeground="#000000",
                  activebackground="#ff8c55",
                  command=self._confirm_shutdown).pack(side="right", padx=2)

        tk.Button(nav, text="↺ Reboot",
                  font=FONT_SMALL, fg="#000000", bg="#ff9500",
                  relief="flat", bd=0, cursor="hand2",
                  padx=12, pady=10,
                  activeforeground="#000000",
                  activebackground="#ffaa30",
                  command=self._confirm_reboot).pack(side="right", padx=2)

        tk.Button(nav, text="ℹ About",
                  font=FONT_SMALL, fg="#000000", bg="#ffa500",
                  relief="flat", bd=0, cursor="hand2",
                  padx=12, pady=10,
                  activeforeground="#000000",
                  activebackground="#ffbb30",
                  command=lambda: self.show_page("about")).pack(
            side="right", padx=2)

        tk.Frame(nav, bg=BORDER, width=1).pack(side="right", fill="y",
                                               pady=8)


        # ── Player bar — packed before content area (Tkinter pack order rule)
        from ui.widgets import PlayerBar as _PB
        self._player_bar = _PB(self.root, self.player)
        self._player_bar.pack(fill="x", side="bottom")

        # Surface a missing/faulty sound card clearly — the UI stays up either way
        if getattr(self.player, "no_audio_device", False):
            try:
                self._player_bar.show_no_audio_device()
            except Exception:
                pass

        # ── Content area ──────────────────────────────────────────────
        self._content_area = tk.Frame(self.root, bg=BG_DARK)
        self._content_area.pack(fill="both", expand=True)

        # Splash — packed first so it fills the content area on startup
        self._splash = SplashScreen(self._content_area)
        self._splash.pack(fill="both", expand=True)
        self.root.update()

        # Instantiate pages
        self._albums_page = AlbumsPage(
            self._content_area, self.library, self.player,
            playlist_manager=self.playlist,
            config=self.config)
        self._albums_page._app_ref = self
        self._playlist_page = PlaylistPage(
            self._content_area, self.playlist,
            library=self.library,
            app=self)
        self._modules_page = ModulesPage(
            self._content_area, self.modules)
        self._equalizer_page = EqualizerPage(
            self._content_area, self.player)
        self._radio_page = RadioPage(
            self._content_area, self.player)
        self._about_page = AboutPage(
            self._content_area, config=self.config)
        self._settings_page = SettingsPage(
            self._content_area, self.config, self.library,
            self.player, on_rescan=self._rescan_library)
        self._video_page = VideoPage(
            self._content_area, config=self.config, app=self)

        self._pages = {
            "albums":     self._albums_page,
            "playlist":   self._playlist_page,
            "video":      self._video_page,
            "modules":    self._modules_page,
            "equalizer":  self._equalizer_page,
            "radio":      self._radio_page,
            "settings":   self._settings_page,
            "about":      self._about_page,
        }

        # Player bar already created above content area
        # Loading overlay removed — splash screen handles this

        # Wire DisplayPanel playlist reference
        if hasattr(self._playlist_page, "_display_panel"):
            dp = self._playlist_page._display_panel
            dp._playlist  = self.playlist

        # Stop button: stop the current track only — stay on the current page
        def _smart_stop():
            self.player.stop()
            self.playlist.defer_autoplay = False

            # Reset player bar
            if hasattr(self, '_player_bar'):
                try:
                    self._player_bar.clear()
                except Exception:
                    pass

            # Notify display panel (VU/visualiser) if the playlist page exists
            dp = getattr(self._playlist_page, "_display_panel", None)
            if dp:
                dp.on_stopped()
            # No page navigation — Stop just stops playback where you are.
        self._smart_stop = _smart_stop
        # Wire stop command directly — store ref on player bar for direct access
        self._player_bar._smart_stop_cmd = self._smart_stop
        # Also wire via set_stop_command in case the button search works
        self.root.after(500, lambda: self._player_bar.set_stop_command(
            self._smart_stop))

        # ── Toast notification ────────────────────────────────────────
        self._toast = tk.Label(self.root, text="",
                               font=FONT_BODY, fg=BG_DARK, bg=ACCENT,
                               padx=16, pady=8)
        self._toast_after = None
        self._active_page = ""

        # Pages stay hidden — splash owns content area until library ready

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _apply_kiosk_cursor(self, widget=None):
        """Recursively hide the mouse cursor on every widget (touchscreen
        kiosk). Overrides the explicit hand2 cursors set on buttons; safe to
        call repeatedly as pages rebuild dynamically."""
        if not getattr(self, "_kiosk_hide_cursor", False):
            return
        w = widget or self.root
        try:
            w.configure(cursor="none")
        except Exception:
            pass
        try:
            for child in w.winfo_children():
                self._apply_kiosk_cursor(child)
        except Exception:
            pass

    def show_page(self, page_id: str):
        if self._active_page == page_id:
            return

        # Deactivate old
        if self._active_page and self._active_page in self._pages:
            self._pages[self._active_page].pack_forget()
            btn = self._nav_buttons.get(self._active_page)
            if btn:
                btn.config(fg="#0a0a0f", bg="#4fc3f7")
                if hasattr(btn, "_wrap"):
                    btn._wrap.config(bg="#000000")
                if hasattr(btn, "_accent_bar"):
                    btn._accent_bar.pack_forget()
        
        # Activate new
        if page_id in self._pages:
            page = self._pages[page_id]
            page.pack(fill="both", expand=True)
            self._active_page = page_id
            btn = self._nav_buttons.get(page_id)
            if btn:
                col = getattr(btn, "_accent_col", ACCENT)
                btn.config(fg="#ffffff", bg=col)
                if hasattr(btn, "_wrap"):
                    btn._wrap.config(bg=col)
                if hasattr(btn, "_accent_bar"):
                    btn._accent_bar.pack(fill="x")

            # Refresh pages that need it when shown.
            # PlaylistPage is excluded — it self-updates via queue_changed events,
            # so calling refresh() here would cause a visible rebuild flash on every visit.
            if hasattr(page, "refresh") and page_id != "playlist":
                self.root.after(10, page.refresh)
            # Video page: scan library on first visit
            if page_id == "video" and not getattr(self._video_page, "_library_ready", False):
                self.root.after(50, self._video_page.refresh)

            # Re-hide the cursor on this page's widgets (kiosk). The delay lets
            # batched/dynamic content (album cards, track rows) be created first.
            if self._kiosk_hide_cursor:
                self.root.after(600, lambda p=page: self._apply_kiosk_cursor(p))

    def refresh_video_library(self):
        """Called by DVDRipper module after a successful rip."""
        self._video_page.refresh()

    # ------------------------------------------------------------------
    # Playlist playback
    # ------------------------------------------------------------------

    def play_playlist(self):
        """Start playlist playback and trigger the display panel."""
        if not self.playlist.pending_selections:
            self.show_notification("Queue is empty — add tracks first", "warning")
            return

        # Navigate to playlist page
        self.show_page("playlist")
        self.playlist.defer_autoplay = False
        self.playlist.start_playback()

        # Trigger the display panel (handles VU / visualiser itself)
        try:
            dp = self._playlist_page._display_panel
            self.root.after(200, dp.on_track_started)
        except AttributeError:
            pass

        # Always return to playlist page and refresh it
        self._active_page = ""
        self._playlist_page.refresh()
        self.show_page("playlist")

    def _confirm_shutdown(self):
        from tkinter import messagebox
        if messagebox.askyesno("Shutdown",
                               "Shut down the system now?",
                               parent=self.root):
            self.player.shutdown()
            import threading, time, subprocess
            def _do():
                time.sleep(0.5)
                try:
                    subprocess.run(["sudo", "shutdown", "-h", "now"],
                                   timeout=10)
                except Exception as e:
                    msg = f"{e}\n\nRun manually: sudo shutdown -h now"
                    self.root.after(0, lambda m=msg: messagebox.showerror(
                        "Shutdown failed", m, parent=self.root))
            threading.Thread(target=_do, daemon=True).start()

    def _confirm_reboot(self):
        from tkinter import messagebox
        if messagebox.askyesno("Reboot",
                               "Reboot the system now?",
                               parent=self.root):
            self.player.shutdown()
            import threading, time, subprocess
            def _do():
                time.sleep(0.5)
                try:
                    subprocess.run(["sudo", "reboot"], timeout=10)
                except Exception as e:
                    msg = f"{e}\n\nRun manually: sudo reboot"
                    self.root.after(0, lambda m=msg: messagebox.showerror(
                        "Reboot failed", m, parent=self.root))
            threading.Thread(target=_do, daemon=True).start()

    # ------------------------------------------------------------------
    # Event wiring
    # ------------------------------------------------------------------

    def _wire_events(self):
        self.player.on_event("track_started", self._on_track_started)
        self.playlist.on_event("queue_changed",
                               lambda: self.root.after(0, self._update_playlist_badge))
        self.player.on_event("track_ended",   self._on_track_ended)
        self.player.on_event("state_changed", self._on_state_changed)
        self.player.on_event("queue_empty",   self._on_queue_empty)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_track_started(self, track: dict):
        self.root.after(0, lambda: self._player_bar.update_track(track))
        self.root.after(0, self._update_playlist_badge)
        # Trigger display panel animation
        if hasattr(self._playlist_page, "_display_panel"):
            self.root.after(0,
                self._playlist_page._display_panel.on_track_started)
        self.modules.broadcast("track_started", track)

    def _update_playlist_badge(self):
        n = self.playlist.selection_count
        btn = self._nav_buttons.get("playlist")
        if btn:
            label = f"♫  Playlist" + (f"  [{n}]" if n > 0 else "")
            btn.config(text=label,
                       fg=ACCENT if n > 0 else TEXT_SECONDARY)

    def _on_track_ended(self):
        self.modules.broadcast("track_ended")

    def _on_state_changed(self, state: str):
        self.modules.broadcast("state_changed", state)

    def _on_queue_empty(self):
        self.modules.broadcast("queue_empty")

    def _on_escape(self, _event=None):
        if self.root.attributes("-fullscreen"):
            self.root.attributes("-fullscreen", False)

    def _on_close(self):
        self.player.shutdown()
        # Checkpoint WAL and close DB cleanly so no duplicate rows on next run
        if hasattr(self, "library") and self.library:
            self.library.close()
        self.root.destroy()

    # ------------------------------------------------------------------
    # Library loading
    # ------------------------------------------------------------------

    def _load_library(self):
        def _scan():
            def _progress(done, total):
                pct = int((done / total) * 70) if total else 0
                self.root.after(0, lambda d=done, t=total, p=pct: (
                    self._splash.set_status(f"Scanning library… {d}/{t} files")
                    if hasattr(self, '_splash') and self._splash and
                    not self._splash._closed else None) or
                    (self._splash.set_progress(10 + p)
                     if hasattr(self, '_splash') and self._splash and
                     not self._splash._closed else None))
            self.root.after(0, lambda: self._splash.set_status(
                "Loading music library…") if hasattr(self, '_splash')
                and self._splash and not self._splash._closed else None)
            self.library.scan(progress_cb=_progress)
            self.root.after(0, self._on_library_ready)

        t = threading.Thread(target=_scan, daemon=True)
        t.start()

    def _on_library_ready(self):
        stats = self.library.get_stats()
        if hasattr(self, '_splash') and self._splash:
            self._splash.set_status(
                f"Ready — {stats['total_tracks']} tracks, "
                f"{stats['total_albums']} albums")
            self._splash.set_progress(100)
            self.root.update_idletasks()
            # Brief pause so user sees "Ready", then dismiss
            self.root.after(600, self._dismiss_splash)
        else:
            self._show_main_ui()

    def _dismiss_splash(self):
        if hasattr(self, '_splash') and self._splash:
            try:
                self._splash.pack_forget()
                self._splash.destroy()
            except Exception:
                pass
            self._splash = None
        self._show_main_ui()

    def _show_main_ui(self):
        """Called once splash is gone — refresh library and show albums."""
        self._albums_page.refresh()
        self.show_page("albums")
        # Warm the album-art thumbnail cache in the background so the first
        # visit to the track-listing/covers views isn't slow. Runs batched on
        # the main thread; starts a moment after the UI settles.
        try:
            self.root.after(1200, self._albums_page.prewarm_thumbnails)
        except Exception:
            pass
        stats = self.library.get_stats()
        self.show_notification(
            f"Library ready: {stats['total_tracks']} tracks, "
            f"{stats['total_albums']} albums", "success")

    def _rescan_library(self):
        new_dir = self.config.get("music_dir")
        self.library.music_dir = new_dir
        # Hide current page
        if self._active_page and self._active_page in self._pages:
            self._pages[self._active_page].pack_forget()
        self._active_page = ""
        # Show fresh splash in content area
        self._splash = SplashScreen(self._content_area)
        self._splash.pack(fill="both", expand=True)
        self._splash.set_status("Rescanning library…")
        self.root.update_idletasks()

        def _scan():
            self.library.scan(force=True)
            self.root.after(0, self._on_library_ready)

        threading.Thread(target=_scan, daemon=True).start()

    # ------------------------------------------------------------------
    # Module loading
    # ------------------------------------------------------------------

    def _load_modules(self):
        def _load():
            self.modules.load_all(self)
            self.root.after(0, self._refresh_plugins_page)

        threading.Thread(target=_load, daemon=True).start()

    def _refresh_plugins_page(self):
        # Rebuild plugins page with loaded modules
        self._modules_page.destroy()
        self._modules_page = ModulesPage(self._content_area, self.modules)
        self._pages["modules"] = self._modules_page
        if self._active_page == "modules":
            self._active_page = ""
            self.show_page("modules")

    # ------------------------------------------------------------------
    # Notification (used by modules too)
    # ------------------------------------------------------------------

    def show_notification(self, message: str, level: str = "info"):
        colours = {
            "info":    (NEON_BLUE, BG_DARK),
            "success": (SUCCESS,   BG_DARK),
            "warning": (WARNING,   BG_DARK),
            "error":   (DANGER,    BG_DARK),
        }
        bg, fg = colours.get(level, (ACCENT, BG_DARK))
        self._toast.config(text=f"  {message}  ", bg=bg, fg=fg)
        self._toast.place(relx=0.5, rely=0.98, anchor="s")
        if self._toast_after:
            self.root.after_cancel(self._toast_after)
        self._toast_after = self.root.after(3500, self._toast.place_forget)

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self):
        self.root.mainloop()


# ---------------------------------------------------------------------------

def main():
    app = PulseBoxApp()
    app.run()


# Entry point



if __name__ == "__main__":
    main()
