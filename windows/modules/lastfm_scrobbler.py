"""
JukeBox Example Module: Last.fm Scrobbler
Demonstrates the complete module API.

To activate: ensure this file is in the /modules/ directory.
Configure your Last.fm API key in JukeBox Settings or directly below.
"""

import tkinter as tk
from core.config import JukeboxModule, APP_VERSION


class LastFmScrobbler(JukeboxModule):
    """
    Example module that would scrobble tracks to Last.fm.
    Shows how to hook into playback events and add a UI panel.
    """

    MODULE_ID          = "lastfm_scrobbler"
    MODULE_NAME        = "Last.fm Scrobbler"
    MODULE_VERSION = APP_VERSION
    MODULE_DESCRIPTION = "Scrobbles played tracks to your Last.fm account."
    MODULE_AUTHOR      = "Andrew J. Morgan"

    def __init__(self):
        super().__init__()
        self._scrobble_count = 0
        self._last_track = None
        self._api_key: str = ""
        self._username: str = ""
        self._count_var: tk.StringVar | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_load(self, app):
        super().on_load(app)
        self._api_key = self.config.get("lastfm_api_key", "")
        self._username = self.config.get("lastfm_username", "")
        print(f"[{self.MODULE_NAME}] Loaded. Username: {self._username or 'not set'}")

    def on_unload(self):
        super().on_unload()
        print(f"[{self.MODULE_NAME}] Unloaded. Total scrobbles this session: {self._scrobble_count}")

    # ------------------------------------------------------------------
    # Playback event hooks
    # ------------------------------------------------------------------

    def on_track_started(self, track: dict):
        self._last_track = track
        artist = track.get("artist", "")
        title = track.get("title", "")
        print(f"[{self.MODULE_NAME}] Now playing: {artist} — {title}")
        # In a real implementation: call Last.fm updateNowPlaying API here

    def on_track_ended(self):
        if self._last_track:
            self._scrobble_count += 1
            if self._count_var:
                self._count_var.set(f"Scrobbles this session: {self._scrobble_count}")
            print(f"[{self.MODULE_NAME}] Scrobbled: {self._last_track.get('title')}")
            # In a real implementation: call Last.fm scrobble API here

    # ------------------------------------------------------------------
    # UI panel shown in the Modules tab
    # ------------------------------------------------------------------

    def get_ui_panel(self, parent) -> tk.Frame:
        from ui.theme import BG_PANEL, BG_CARD, ACCENT, TEXT_PRIMARY, TEXT_SECONDARY, FONT_BODY, FONT_SMALL

        frame = tk.Frame(parent, bg=BG_PANEL)

        # Username row
        row1 = tk.Frame(frame, bg=BG_PANEL)
        row1.pack(fill="x", pady=2)
        tk.Label(row1, text="Username:", font=FONT_SMALL,
                 fg=TEXT_PRIMARY, bg=BG_PANEL, width=12, anchor="w").pack(side="left")
        user_var = tk.StringVar(value=self._username)
        tk.Entry(row1, textvariable=user_var,
                 font=FONT_BODY, bg=BG_CARD, fg=TEXT_PRIMARY,
                 insertbackground=ACCENT, relief="flat").pack(
            side="left", fill="x", expand=True, ipady=3)

        # API key row
        row2 = tk.Frame(frame, bg=BG_PANEL)
        row2.pack(fill="x", pady=2)
        tk.Label(row2, text="API Key:", font=FONT_SMALL,
                 fg=TEXT_PRIMARY, bg=BG_PANEL, width=12, anchor="w").pack(side="left")
        key_var = tk.StringVar(value=self._api_key)
        tk.Entry(row2, textvariable=key_var,
                 font=FONT_BODY, bg=BG_CARD, fg=TEXT_PRIMARY,
                 insertbackground=ACCENT, show="*", relief="flat").pack(
            side="left", fill="x", expand=True, ipady=3)

        # Scrobble counter
        self._count_var = tk.StringVar(
            value=f"Scrobbles this session: {self._scrobble_count}")
        tk.Label(frame, textvariable=self._count_var,
                 font=FONT_SMALL, fg=ACCENT, bg=BG_PANEL).pack(anchor="w", pady=4)

        # Save button
        def _save():
            self._username = user_var.get()
            self._api_key = key_var.get()
            self.config.set("lastfm_username", self._username)
            self.config.set("lastfm_api_key", self._api_key)
            self.emit_notification("Last.fm credentials saved.", "success")

        tk.Button(frame, text="Save Credentials",
                  font=FONT_SMALL, fg="#0a0a0f", bg=ACCENT,
                  relief="flat", bd=0, padx=10, pady=4,
                  cursor="hand2", command=_save).pack(anchor="w")

        return frame
