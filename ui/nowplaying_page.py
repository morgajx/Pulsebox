"""
JukeBox Now Playing & Queue Page
"""

import tkinter as tk
from .theme import *
from .widgets import ScrollableFrame


class NowPlayingPage(tk.Frame):
    def __init__(self, parent, player, **kwargs):
        super().__init__(parent, bg=BG_DARK, **kwargs)
        self._player = player
        self._build()
        player.on_event("track_started", lambda t: self.after(0, self.refresh))
        player.on_event("track_ended", lambda: self.after(0, self.refresh))
        player.on_event("queue_empty", lambda: self.after(0, self.refresh))

    def _build(self):
        tk.Label(self, text="NOW PLAYING", font=FONT_DISPLAY,
                 fg=ACCENT, bg=BG_DARK).pack(anchor="w", padx=PAD, pady=PAD)

        # Current track card
        self._card = tk.Frame(self, bg=BG_PANEL, padx=PAD * 2, pady=PAD)
        self._card.pack(fill="x", padx=PAD, pady=(0, PAD))

        self._lbl_title = tk.Label(self._card, text="Nothing playing",
                                   font=FONT_DISPLAY, fg=NEON_BLUE, bg=BG_PANEL,
                                   anchor="w")
        self._lbl_title.pack(fill="x")
        self._lbl_artist = tk.Label(self._card, text="",
                                    font=FONT_HEADING, fg=ACCENT, bg=BG_PANEL,
                                    anchor="w")
        self._lbl_artist.pack(fill="x")
        self._lbl_album = tk.Label(self._card, text="",
                                   font=FONT_BODY, fg=TEXT_PRIMARY, bg=BG_PANEL,
                                   anchor="w")
        self._lbl_album.pack(fill="x")

        # Queue section
        tk.Label(self, text="UP NEXT", font=FONT_SUBHEAD,
                 fg=TEXT_PRIMARY, bg=BG_DARK).pack(
            anchor="w", padx=PAD, pady=(0, 4))

        self._queue_frame = ScrollableFrame(self, bg=BG_DARK)
        self._queue_frame.pack(fill="both", expand=True)

        self.refresh()

    def refresh(self):
        track = self._player.get_current_track()
        if track:
            self._lbl_title.config(text=track.get("title", "Unknown"))
            self._lbl_artist.config(text=track.get("artist", ""))
            self._lbl_album.config(text=track.get("album", ""))
        else:
            self._lbl_title.config(text="Nothing playing")
            self._lbl_artist.config(text="")
            self._lbl_album.config(text="")

        # Queue
        for w in self._queue_frame.inner.winfo_children():
            w.destroy()

        queue = self._player.get_queue()
        current_idx = self._player.get_current_index()

        if not queue:
            tk.Label(self._queue_frame.inner,
                     text="Queue is empty", font=FONT_BODY,
                     fg=TEXT_PRIMARY, bg=BG_DARK).pack(padx=PAD, pady=PAD)
            return

        for i, t in enumerate(queue):
            is_current = (i == current_idx)
            is_past = (i < current_idx)
            bg = BG_CARD if i % 2 == 0 else BG_PANEL
            row = tk.Frame(self._queue_frame.inner, bg=bg)
            row.pack(fill="x", padx=PAD_SM, pady=1)

            marker = "▶" if is_current else f"  {i + 1}"
            fg_marker = NEON_BLUE if is_current else TEXT_DIM
            tk.Label(row, text=marker, font=FONT_SMALL,
                     fg=fg_marker, bg=bg, width=4).pack(side="left")

            fg_title = NEON_BLUE if is_current else (TEXT_DIM if is_past else TEXT_PRIMARY)
            tk.Label(row, text=t.get("title", "?"),
                     font=FONT_BODY, fg=fg_title,
                     bg=bg, anchor="w").pack(side="left", fill="x", expand=True)

            tk.Label(row, text=t.get("artist", ""),
                     font=FONT_SMALL, fg=TEXT_PRIMARY, bg=bg).pack(
                side="right", padx=8)

        # Clear queue button
        tk.Button(self._queue_frame.inner, text="Clear Queue",
                  font=FONT_SMALL, fg=DANGER, bg=BG_DARK,
                  relief="flat", bd=0, cursor="hand2",
                  command=self._clear_queue).pack(pady=PAD, anchor="w", padx=PAD)

    def _clear_queue(self):
        self._player.clear_queue()
        self.refresh()
