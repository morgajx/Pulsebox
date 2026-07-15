"""
PulseBox Playlist Page
Left panel:  live jukebox selection queue (up to 25 slots)
Right panel: saved playlists library
"""

import tkinter as tk
from tkinter import simpledialog, messagebox

from .theme import *
from .display_panel import DisplayPanel
from .widgets import ScrollableFrame


class PlaylistPage(tk.Frame):
    def __init__(self, parent, playlist_manager, library=None, app=None, **kwargs):
        super().__init__(parent, bg=BG_DARK, **kwargs)
        self._pm      = playlist_manager
        self._library = library
        self._app     = app
        self._build()

        # Refresh whenever the queue changes
        playlist_manager.on_event("queue_changed",
                                   lambda: self.after(0, self._refresh_queue))
        playlist_manager.on_event("playback_mode_changed",
                                   lambda m: self.after(0, self._refresh_mode_badge))

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build(self):
        # ── Header ────────────────────────────────────────────────────
        hdr = tk.Frame(self, bg=BG_DARK)
        hdr.pack(fill="x", padx=PAD, pady=(PAD, 4))
        tk.Label(hdr, text="PLAYLISTS", font=FONT_DISPLAY,
                 fg=ACCENT, bg=BG_DARK).pack(side="left")

        self._mode_badge = tk.Label(hdr, text="", font=FONT_SMALL,
                                    fg=NEON_BLUE, bg=BG_DARK)
        self._mode_badge.pack(side="right", padx=PAD)


        self._refresh_mode_badge()

        # ── Two-panel split ───────────────────────────────────────────
        split = tk.Frame(self, bg=BG_DARK)
        split.pack(fill="both", expand=True, padx=PAD, pady=(0, PAD))

        # Left — live queue
        left = tk.Frame(split, bg=BG_DARK)
        left.pack(side="left", fill="both", expand=True, padx=(0, PAD_SM))
        self._build_queue_panel(left)

        # Divider
        tk.Frame(split, bg=BORDER, width=1).pack(side="left", fill="y")

        # Right — saved playlists (top) + display panel (bottom)
        right = tk.Frame(split, bg=BG_DARK)
        right.pack(side="left", fill="both", expand=True, padx=(PAD_SM, 0))

        # Saved playlists in top portion
        saved_frame = tk.Frame(right, bg=BG_DARK)
        saved_frame.pack(fill="both", expand=True)
        self._build_saved_panel(saved_frame)

        tk.Frame(right, bg=BORDER, height=1).pack(fill="x")

        # Display panel (VU / visualiser)
        _player   = self._app.player   if self._app else None
        _playlist = self._app.playlist if self._app else None
        self._display_panel = DisplayPanel(
            right,
            player=_player,
            playlist=_playlist,
            on_hide=None)   # wired later by app
        self._display_panel.pack(fill="both", expand=True)

    # ------------------------------------------------------------------
    # Live Queue Panel
    # ------------------------------------------------------------------

    def _build_queue_panel(self, parent):
        # Sub-header
        sh = tk.Frame(parent, bg=BG_DARK)
        sh.pack(fill="x", pady=(0, PAD_SM))

        self._slot_lbl = tk.Label(sh, text="", font=FONT_SUBHEAD,
                                  fg=TEXT_PRIMARY, bg=BG_DARK)
        self._slot_lbl.pack(side="left")

        # Action buttons
        btn_row = tk.Frame(sh, bg=BG_DARK)
        btn_row.pack(side="right")

        # ── Play button — starts record animation + audio ──────────
        self._play_btn = tk.Button(
            btn_row, text="▶ Play",
            font=FONT_SMALL, fg=BG_DARK, bg=ACCENT,
            relief="flat", bd=0, cursor="hand2", padx=10, pady=4,
            command=self._play_playlist)
        self._play_btn.pack(side="left", padx=2)

        tk.Button(btn_row, text="💾 Save",
                  font=FONT_SMALL, fg=ACCENT, bg=BG_CARD,
                  relief="flat", bd=0, cursor="hand2", padx=8, pady=4,
                  command=self._save_playlist).pack(side="left", padx=2)

        tk.Button(btn_row, text="↥ Load",
                  font=FONT_SMALL, fg=NEON_BLUE, bg=BG_CARD,
                  relief="flat", bd=0, cursor="hand2", padx=8, pady=4,
                  activeforeground=NEON_BLUE,
                  activebackground=BG_CARD,
                  command=self._load_playlist_dialog).pack(side="left", padx=2)

        tk.Button(btn_row, text="✕ Clear",
                  font=FONT_SMALL, fg=DANGER, bg=BG_CARD,
                  relief="flat", bd=0, cursor="hand2", padx=8, pady=4,
                  command=self._clear_queue).pack(side="left", padx=2)

        # Slot meter bar
        self._meter_frame = tk.Frame(parent, bg=BG_PANEL, height=18)
        self._meter_frame.pack(fill="x", pady=(0, PAD_SM))
        self._meter_frame.pack_propagate(False)
        self._meter_canvas = tk.Canvas(self._meter_frame, bg=BG_PANEL,
                                       highlightthickness=0, height=18)
        self._meter_canvas.pack(fill="both", expand=True)
        self._meter_canvas.bind("<Configure>", lambda e: self._draw_meter())

        # Queue scroll list
        self._queue_scroll = ScrollableFrame(parent, bg=BG_DARK)
        self._queue_scroll.pack(fill="both", expand=True)

        self._refresh_queue()

    def _refresh_queue(self):
        pending  = self._pm.pending_selections
        played   = self._pm.played_selections
        slots    = self._pm.slots_remaining
        total    = len(pending) + len(played)

        self._slot_lbl.config(
            text=f"Queue  {len(pending)}/{MAX_SELECTIONS}")
        self._draw_meter()

        # Dim the play button when nothing is queued
        if hasattr(self, "_play_btn"):
            if pending:
                self._play_btn.config(bg=ACCENT, fg=BG_DARK, state="normal")
            else:
                self._play_btn.config(bg=BG_CARD, fg=TEXT_PRIMARY, state="disabled")

        inner = self._queue_scroll.inner
        for w in inner.winfo_children():
            w.destroy()

        if not pending and not played:
            tk.Label(inner,
                     text="No selections.\nBrowse albums and press ＋ on any track.",
                     font=FONT_BODY, fg=TEXT_PRIMARY, bg=BG_DARK,
                     justify="center").pack(expand=True, pady=PAD * 3)

            # Show album-continuation hint
            tk.Label(inner,
                     text="▶  Album continuation mode active",
                     font=FONT_SMALL, fg=NEON_BLUE, bg=BG_DARK).pack()
            tk.Label(inner,
                     text="Tracks play sequentially through the current album.",
                     font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_DARK).pack()
            return

        # Played tracks (dimmed, at top)
        if played:
            tk.Label(inner, text="PLAYED", font=FONT_TINY,
                     fg=TEXT_PRIMARY, bg=BG_DARK).pack(anchor="w",
                                                    padx=PAD_SM, pady=(4, 0))
            for sel in played[-5:]:   # show last 5 only
                self._track_row(inner, sel, is_played=True, index=None)

        # Pending tracks
        if pending:
            tk.Label(inner, text="UP NEXT", font=FONT_TINY,
                     fg=TEXT_PRIMARY, bg=BG_DARK).pack(anchor="w",
                                                          padx=PAD_SM,
                                                          pady=(8, 0))
            for i, sel in enumerate(pending):
                self._track_row(inner, sel, is_played=False, index=i)

        self._queue_scroll.bind_all_children_scroll(inner)

    def _track_row(self, parent, sel, is_played: bool, index):
        bg   = BG_PANEL if (index is None or index % 2 == 0) else BG_CARD
        row  = tk.Frame(parent, bg=bg)
        row.pack(fill="x", padx=PAD_SM, pady=1)

        # Position badge or ✓
        if is_played:
            badge_text = "✓"
            badge_fg   = TEXT_DIM
        else:
            badge_text = str(index + 1)
            badge_fg   = ACCENT

        tk.Label(row, text=badge_text, font=FONT_SUBHEAD,
                 fg=badge_fg, bg=bg, width=3).pack(side="left", padx=(4, 2))

        # ── Album art thumbnail (36x36 — same as tree mode) ──────────
        art_canvas = tk.Canvas(row, width=36, height=36,
                               bg=bg, highlightthickness=0)
        art_canvas.pack(side="left", padx=(0, 6), pady=3)
        self._draw_track_art(art_canvas, sel.track, bg)

        # Track info
        info = tk.Frame(row, bg=bg)
        info.pack(side="left", fill="x", expand=True, padx=4)

        title_fg = TEXT_DIM if is_played else TEXT_PRIMARY
        tk.Label(info, text=sel.track.get("title", "?"),
                 font=FONT_BODY, fg=title_fg, bg=bg,
                 anchor="w").pack(fill="x")
        tk.Label(info,
                 text=f"{sel.track.get('artist','')} — {sel.track.get('album','')}",
                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=bg,
                 anchor="w").pack(fill="x")

        # Remove button (pending only)
        if not is_played and index is not None:
            tk.Button(row, text="✕", font=FONT_TINY,
                      fg=DANGER, bg=bg, relief="flat", bd=0,
                      cursor="hand2", padx=6,
                      command=lambda i=index: self._remove_selection(i)
                      ).pack(side="right", padx=4)

    def _draw_track_art(self, canvas, track: dict, bg: str):
        """Draw 36x36 album art on canvas, deferred so widget is mapped first."""
        def _do():
            from .image_loader import load_image_tk
            art = None
            if self._library:
                artist = track.get("artist", "")
                album  = track.get("album", "")
                for alb in self._library.get_all_albums():
                    if alb.get("artist") == artist and alb.get("name") == album:
                        art = alb.get("art")
                        break
            if art:
                photo = load_image_tk(art, 36)
                if photo:
                    canvas._photo = photo
                    canvas.delete("all")
                    canvas.create_image(18, 18, image=photo, anchor="center")
                    return
            canvas.create_rectangle(0, 0, 36, 36, fill=BG_PANEL, outline=BORDER)
            canvas.create_text(18, 18, text="♪",
                               font=("Helvetica", 14), fill=BORDER_ACCENT)
        canvas.after(1, _do)

    def _draw_meter(self):
        c = self._meter_canvas
        w = c.winfo_width()
        h = 18
        if w < 2:
            return
        c.delete("all")
        c.create_rectangle(0, 0, w, h, fill=BG_PANEL, outline="")

        total    = MAX_SELECTIONS
        used     = self._pm.selection_count
        slot_w   = w / total
        gap      = 2

        for i in range(total):
            x1 = i * slot_w + gap
            x2 = (i + 1) * slot_w - gap
            if i < used:
                # Colour shifts red as it fills up
                ratio = used / total
                if ratio < 0.5:
                    colour = ACCENT
                elif ratio < 0.8:
                    colour = WARNING
                else:
                    colour = DANGER
            else:
                colour = BORDER
            c.create_rectangle(x1, 3, x2, h - 3,
                                fill=colour, outline="")

        remaining = total - used
        c.create_text(w // 2, h // 2,
                      text=f"{remaining} slot{'s' if remaining != 1 else ''} remaining",
                      font=("Helvetica", 8), fill=TEXT_SECONDARY)

    def _play_playlist(self):
        """Start playback of the pending queue."""
        if self._app:
            self._app.play_playlist()
        else:
            # Fallback if no app ref — just start audio
            self._pm.start_playback()

    def _remove_selection(self, index: int):
        self._pm.remove_selection(index)

    def _clear_queue(self):
        if messagebox.askyesno("Clear Queue",
                               "Remove all pending selections?",
                               parent=self):
            self._pm.clear_selections()

    def _save_playlist(self):
        if not self._pm.pending_selections:
            messagebox.showinfo("Save Playlist",
                                "Nothing in queue to save.", parent=self)
            return
        name = simpledialog.askstring(
            "Save Playlist", "Playlist name:",
            parent=self,
            initialvalue=f"My Mix {len(self._pm.get_saved_playlists()) + 1}")
        if name:
            ok, msg = self._pm.save_playlist(name)
            if ok:
                self._refresh_saved()
            messagebox.showinfo("Save Playlist", msg, parent=self)

    def _refresh_mode_badge(self):
        mode = self._pm.mode
        if mode == "selections":
            self._mode_badge.config(
                text="● JUKEBOX MODE", fg=ACCENT)
        else:
            self._mode_badge.config(
                text="● ALBUM CONTINUATION", fg=NEON_BLUE)

    # ------------------------------------------------------------------
    # Saved Playlists Panel
    # ------------------------------------------------------------------

    def _build_saved_panel(self, parent):
        sh = tk.Frame(parent, bg=BG_DARK)
        sh.pack(fill="x", pady=(0, PAD_SM))
        tk.Label(sh, text="Saved Playlists", font=FONT_SUBHEAD,
                 fg=TEXT_PRIMARY, bg=BG_DARK).pack(side="left")

        self._saved_scroll = ScrollableFrame(parent, bg=BG_DARK)
        self._saved_scroll.pack(fill="both", expand=True)

        # Defer so the frame is fully laid out before populating
        self.after(50, self._refresh_saved)

    def _refresh_saved(self):
        inner = self._saved_scroll.inner
        for w in inner.winfo_children():
            w.destroy()

        playlists = self._pm.get_saved_playlists()

        if not playlists:
            tk.Label(inner,
                     text="No saved playlists yet.\nFill the queue and press 💾 Save.",
                     font=FONT_BODY, fg=TEXT_PRIMARY, bg=BG_DARK,
                     justify="center").pack(expand=True, pady=PAD * 3)
            return

        for pl in reversed(playlists):   # newest first
            self._playlist_card(inner, pl)

        self._saved_scroll.bind_all_children_scroll(inner)

    def _playlist_card(self, parent, pl: dict):
        card = tk.Frame(parent, bg=BG_PANEL, padx=PAD, pady=PAD_SM)
        card.pack(fill="x", padx=PAD_SM, pady=3)

        # Title row
        title_row = tk.Frame(card, bg=BG_PANEL)
        title_row.pack(fill="x")

        tk.Label(title_row, text=f"♫  {pl['name']}",
                 font=FONT_SUBHEAD, fg=TEXT_PRIMARY,
                 bg=BG_PANEL).pack(side="left")

        # Action buttons
        btn_frame = tk.Frame(title_row, bg=BG_PANEL)
        btn_frame.pack(side="right")

        name = pl["name"]

        tk.Button(btn_frame, text="▶ Load",
                  font=FONT_TINY, fg=BG_DARK, bg=ACCENT,
                  relief="flat", bd=0, cursor="hand2", padx=6, pady=3,
                  command=lambda n=name: self._load_playlist(n)
                  ).pack(side="left", padx=2)

        tk.Button(btn_frame, text="✎",
                  font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_CARD,
                  relief="flat", bd=0, cursor="hand2", padx=6, pady=3,
                  command=lambda n=name: self._rename_playlist(n)
                  ).pack(side="left", padx=2)

        tk.Button(btn_frame, text="✕",
                  font=FONT_TINY, fg=DANGER, bg=BG_CARD,
                  relief="flat", bd=0, cursor="hand2", padx=6, pady=3,
                  command=lambda n=name: self._delete_playlist(n)
                  ).pack(side="left", padx=2)

        # Meta
        tk.Label(card,
                 text=f"{pl.get('count', 0)} tracks  ·  {pl.get('created', '')}",
                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_PANEL).pack(anchor="w")

        # Track preview (first 5)
        tracks = pl.get("tracks", [])
        for t in tracks[:5]:
            tk.Label(card,
                     text=f"  {t.get('title','?')}  —  {t.get('artist','')}",
                     font=FONT_TINY, fg=TEXT_PRIMARY,
                     bg=BG_PANEL, anchor="w").pack(fill="x")
        if len(tracks) > 5:
            tk.Label(card, text=f"  … and {len(tracks) - 5} more",
                     font=FONT_TINY, fg=TEXT_PRIMARY,
                     bg=BG_PANEL, anchor="w").pack(fill="x")

    def _load_playlist_dialog(self):
        """Show a dialog listing all saved playlists to choose from."""
        playlists = self._pm.get_saved_playlists()
        if not playlists:
            from tkinter import messagebox
            messagebox.showinfo("No Saved Playlists",
                                "No saved playlists yet.\nFill the queue and press 💾 Save.",
                                parent=self)
            return

        dialog = tk.Toplevel(self)
        dialog.title("Load Playlist")
        dialog.configure(bg=BG_DARK)
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)

        # Centre over main window
        self.update_idletasks()
        x = self.winfo_rootx() + self.winfo_width() // 2 - 200
        y = self.winfo_rooty() + self.winfo_height() // 2 - 150
        dialog.geometry(f"400x350+{x}+{y}")

        tk.Label(dialog, text="Select a playlist to load:",
                 font=FONT_SUBHEAD, fg=ACCENT, bg=BG_DARK).pack(
            pady=(PAD, PAD_SM), padx=PAD, anchor="w")

        # Scrollable list
        from ui.widgets import ScrollableFrame
        scroll = ScrollableFrame(dialog, bg=BG_DARK)
        scroll.pack(fill="both", expand=True, padx=PAD)

        def _pick(name):
            self._load_playlist(name)
            dialog.destroy()

        for pl in reversed(playlists):
            name = pl["name"]
            track_count = len(pl.get("tracks", []))
            row = tk.Frame(scroll.inner, bg=BG_PANEL, pady=4)
            row.pack(fill="x", pady=2)
            info = tk.Frame(row, bg=BG_PANEL)
            info.pack(side="left", fill="x", expand=True, padx=8)
            tk.Label(info, text=f"♫  {name}",
                     font=FONT_BODY, fg=TEXT_PRIMARY,
                     bg=BG_PANEL, anchor="w").pack(fill="x")
            tk.Label(info, text=f"{track_count} tracks",
                     font=FONT_TINY, fg=TEXT_PRIMARY,
                     bg=BG_PANEL, anchor="w").pack(fill="x")
            tk.Button(row, text="▶ Load",
                      font=FONT_TINY, fg=BG_DARK, bg=ACCENT,
                      relief="flat", bd=0, cursor="hand2",
                      padx=8, pady=4,
                      activeforeground=BG_DARK,
                      activebackground=ACCENT,
                      command=lambda n=name: _pick(n)).pack(
                side="right", padx=8)

        tk.Button(dialog, text="Cancel",
                  font=FONT_SMALL, fg=TEXT_PRIMARY, bg=BG_CARD,
                  relief="flat", bd=0, cursor="hand2",
                  padx=16, pady=6,
                  activeforeground=TEXT_PRIMARY,
                  activebackground=BG_CARD,
                  command=dialog.destroy).pack(pady=PAD)

    def _load_playlist(self, name: str):
        ok, msg = self._pm.load_playlist(name)
        if ok:
            self._refresh_queue()
        messagebox.showinfo("Load Playlist", msg, parent=self)

    def _rename_playlist(self, name: str):
        new = simpledialog.askstring("Rename Playlist", "New name:",
                                     parent=self, initialvalue=name)
        if new and new != name:
            ok, msg = self._pm.rename_playlist(name, new)
            if ok:
                self._refresh_saved()
            else:
                messagebox.showerror("Rename", msg, parent=self)

    def _delete_playlist(self, name: str):
        if messagebox.askyesno("Delete Playlist",
                               f"Delete '{name}'?", parent=self):
            self._pm.delete_playlist(name)
            self._refresh_saved()

    # ------------------------------------------------------------------
    # Public refresh
    # ------------------------------------------------------------------

    def refresh(self):
        """Called when the playlist tab is made visible."""
        self._refresh_queue()
        self._refresh_saved()
        self._refresh_mode_badge()


# Expose constant for the queue panel to reference
MAX_SELECTIONS = 25
