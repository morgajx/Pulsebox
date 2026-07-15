"""
PulseBox Albums Page
  - A-Z bar and content hidden until library is ready
  - Hero banner above track list when album is selected
  - Cover grid and Artist/Album tree browse modes
  - Remembers last played album
  - Track rows with + queue button
"""

import io
import tkinter as tk
from tkinter import messagebox

from .theme import *
from .theme import get_scale
from .widgets import ScrollableFrame, AlbumCard, make_track_art_canvas


# ---------------------------------------------------------------------------
# Hero Banner
# ---------------------------------------------------------------------------

class AlbumsPage(tk.Frame):

    def __init__(self, parent, library, player,
                 playlist_manager=None, config=None, **kwargs):
        super().__init__(parent, bg=BG_DARK, **kwargs)
        self._library      = library
        self._player       = player
        self._pm           = playlist_manager
        self._config       = config
        self._selected_letter    = "A"
        self._current_album_view = None
        self._browse_mode        = "covers"
        self._app_ref            = None
        self._library_ready      = False
        self._overlay_active     = False
        self._render_token       = 0    # cancels superseded batched renders

        self._build()

        # Single track_started callback — registered once only
        def _on_track_start(t):
            try:
                if not self._current_album_view or not self._library_ready:
                    return
                if self._overlay_active:
                    # Stay on overlay, just refresh text labels
                    self.after(0, lambda alb=self._current_album_view:
                               self._refresh_overlay_labels(alb, t))
                else:
                    self.after(0, lambda alb=self._current_album_view:
                               self._show_album_detail(alb))
            except Exception:
                pass
        self._player.on_event("track_started", _on_track_start)

    # ------------------------------------------------------------------
    # Build — header always visible; A-Z + content hidden until ready
    # ------------------------------------------------------------------

    def _build(self):
        # ── Header row (always shown) ─────────────────────────────────
        header = tk.Frame(self, bg=BG_DARK)
        header.pack(fill="x", padx=PAD, pady=(PAD_SM, 2))

        tk.Label(header, text="MUSIC LIBRARY", font=FONT_DISPLAY,
                 fg=ACCENT, bg=BG_DARK).pack(side="left")

        toggle_frame = tk.Frame(header, bg=BG_DARK)
        toggle_frame.pack(side="right")
        tk.Label(toggle_frame, text="View:", font=FONT_SMALL,
                 fg=TEXT_PRIMARY, bg=BG_DARK).pack(side="left", padx=(0, 4))

        self._mode_var = tk.StringVar(value="covers")
        for label, value in [("🖼 Covers", "covers"), ("📋 Tree", "tree")]:
            tk.Radiobutton(
                toggle_frame, text=label, variable=self._mode_var,
                value=value, font=FONT_SMALL,
                fg=TEXT_PRIMARY, bg=BG_DARK,
                activebackground=BG_DARK, selectcolor=BG_CARD,
                cursor="hand2", command=self._on_mode_change
            ).pack(side="left", padx=4)

        self._lbl_stats = tk.Label(header, text="", font=FONT_SMALL,
                                   fg=TEXT_PRIMARY, bg=BG_DARK)
        self._lbl_stats.pack(side="right", anchor="s", pady=4, padx=12)

        # ── Search bar — styled like radio page ──────────────────────
        # Search bar removed — space reclaimed for album grid
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", self._on_search)
        self._search_ent = None   # no widget but _clear_search still safe

        # ── A-Z bar — hidden until library ready ─────────────────────
        self._az_frame = tk.Frame(self, bg=BG_PANEL)
        # NOT packed yet — packed in refresh() after library loads
        self._az_buttons: dict[str, tk.Button] = {}

        # ── Content area — hidden until library ready ─────────────────
        self._content = tk.Frame(self, bg=BG_DARK)
        # NOT packed yet

        # ── Loading placeholder ───────────────────────────────────────
        self._loading_placeholder = tk.Frame(self, bg=BG_DARK)
        self._loading_placeholder.pack(fill="both", expand=True)
        tk.Label(self._loading_placeholder,
                 text="Loading library…",
                 font=FONT_HEADING, fg=TEXT_PRIMARY, bg=BG_DARK).pack(
            expand=True)

    # ------------------------------------------------------------------
    # A-Z bar
    # ------------------------------------------------------------------

    def _build_az_bar(self):
        for w in self._az_frame.winfo_children():
            w.destroy()
        self._az_buttons.clear()

        letters   = ["ALL", "#"] + [chr(c) for c in range(ord("A"), ord("Z") + 1)]
        available = set(self._library.get_alphabet_index())

        for letter in letters:
            is_avail = (letter == "ALL") or (letter in available)
            active   = (letter == self._selected_letter)
            btn = tk.Button(
                self._az_frame,
                text=letter,
                font=FONT_AZ if letter == "ALL" else ("Helvetica", 11, "bold"),
                fg=ACCENT if active else (TEXT_PRIMARY if is_avail else TEXT_DIM),
                bg=BG_CARD if active else BG_PANEL,
                relief="flat", bd=0,
                width=3 if letter == "ALL" else 2,
                cursor="hand2" if is_avail else "arrow",
                padx=2,
                command=(lambda l=letter: self._select_letter(l))
                if is_avail else None,
            )
            btn.pack(side="left", padx=1, pady=4)
            self._az_buttons[letter] = btn

    def _select_letter(self, letter):
        self._selected_letter    = letter
        self._current_album_view = None
        self._search_var.set("")
        self._build_az_bar()
        self._show_content()

    # ------------------------------------------------------------------
    # Mode toggle
    # ------------------------------------------------------------------

    def _on_mode_change(self):
        self._browse_mode = self._mode_var.get()
        if self._config:
            self._config.set("browse_mode", self._browse_mode)
        self._current_album_view = None
        self._show_content()

    # ------------------------------------------------------------------
    # Content dispatcher
    # ------------------------------------------------------------------

    def _show_content(self, albums=None):
        if not self._library_ready:
            return
        if self._browse_mode == "tree":
            self._show_tree_view(albums)
        else:
            self._show_album_grid(albums)

    # ------------------------------------------------------------------
    # Cover Grid
    # ------------------------------------------------------------------

    def _show_album_grid(self, albums=None):
        # Guard against re-entrant calls (e.g. multiple A-Z button firings)
        if getattr(self, "_rendering", False):
            return
        self._rendering = True
        try:
            self._show_album_grid_inner(albums)
        finally:
            self._rendering = False

    def _show_album_grid_inner(self, albums=None):
        for w in self._content.winfo_children():
            w.destroy()

        if albums is None:
            if self._selected_letter == "ALL":
                albums = self._library.get_all_albums()
            else:
                albums = self._library.get_albums_by_letter(
                    self._selected_letter)

        if not albums:
            self._empty_label(f"No albums found for '{self._selected_letter}'")
            return

        scroll = ScrollableFrame(self._content, bg=BG_DARK)
        scroll.pack(fill="both", expand=True)

        # Columns from config; scale may allow fewer on small screens
        cols = self._config.get("columns_per_row", 7) if self._config else 7

        # Render in batches so a large 'ALL' list doesn't block the UI thread.
        # Each batch checks the render token; if the user has since changed
        # letter or view mode, the stale batches stop immediately.
        self._render_token += 1
        token = self._render_token
        batch = max(cols * 3, 12)

        def _render_batch(start):
            if token != self._render_token:
                return  # superseded by a newer render
            try:
                if not scroll.inner.winfo_exists():
                    return
            except tk.TclError:
                return
            end = min(start + batch, len(albums))
            for i in range(start, end):
                r, c2 = divmod(i, cols)
                card = AlbumCard(scroll.inner, albums[i],
                                 on_click=self._on_album_click)
                card.grid(row=r, column=c2,
                          padx=PAD_SM, pady=PAD_SM, sticky="nw")
            if end < len(albums):
                self.after(1, lambda: _render_batch(end))
            else:
                # Finished — wire scrolling and refresh the scroll region once
                scroll.clear_scroll_bindings()
                scroll.bind_all_children_scroll(scroll.inner)
                scroll.refresh_scrollregion()
                self._update_stats()

        _render_batch(0)

    # ------------------------------------------------------------------
    # Tree view
    # ------------------------------------------------------------------

    def _show_tree_view(self, albums=None):
        for w in self._content.winfo_children():
            w.destroy()

        if albums is not None:
            self._show_tree_flat(albums)
            return

        scroll = ScrollableFrame(self._content, bg=BG_DARK)
        scroll.pack(fill="both", expand=True)

        all_albums = (self._library.get_all_albums()
                      if self._selected_letter == "ALL"
                      else self._library.get_albums_by_letter(
                          self._selected_letter))

        if not all_albums:
            tk.Label(scroll.inner,
                     text=f"No albums found for '{self._selected_letter}'",
                     font=FONT_BODY, fg=TEXT_PRIMARY, bg=BG_DARK).pack(
                padx=PAD, pady=PAD)
            return

        by_artist: dict[str, list] = {}
        for alb in all_albums:
            by_artist.setdefault(alb["artist"], []).append(alb)

        for artist in sorted(by_artist.keys(), key=str.upper):
            self._tree_artist_row(scroll.inner, artist, by_artist[artist])

        scroll.bind_all_children_scroll(scroll.inner)
        self._update_stats()

    def _tree_artist_row(self, parent, artist, albums):
        artist_frame = tk.Frame(parent, bg=BG_DARK)
        artist_frame.pack(fill="x", pady=(4, 0))

        hdr = tk.Frame(artist_frame, bg=BG_PANEL)
        hdr.pack(fill="x")

        toggle_var  = tk.BooleanVar(value=True)
        albums_frame = tk.Frame(artist_frame, bg=BG_DARK)
        albums_frame.pack(fill="x")

        def _toggle(f=albums_frame, v=toggle_var, b=None):
            v.set(not v.get())
            f.pack(fill="x") if v.get() else f.pack_forget()
            if b:
                b.config(text="▾" if v.get() else "▸")

        toggle_btn = tk.Button(
            hdr, text="▾", font=FONT_SMALL,
            fg=ACCENT, bg=BG_PANEL, relief="flat", bd=0,
            cursor="hand2", padx=6,
            command=lambda: _toggle(albums_frame, toggle_var, toggle_btn))
        toggle_btn.pack(side="left")

        tk.Label(hdr, text=artist, font=FONT_SUBHEAD,
                 fg=TEXT_PRIMARY, bg=BG_PANEL,
                 anchor="w").pack(side="left", fill="x",
                                  expand=True, padx=4, pady=6)
        tk.Label(hdr,
                 text=f"{len(albums)} album{'s' if len(albums)!=1 else ''}",
                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_PANEL).pack(
            side="right", padx=8)

        for alb in sorted(albums, key=lambda a: a.get("name", "").upper()):
            self._tree_album_row(albums_frame, alb)

    def _tree_album_row(self, parent, album):
        bg  = BG_CARD
        row = tk.Frame(parent, bg=bg, cursor="hand2")
        row.pack(fill="x", padx=(24, 0), pady=1)

        art_c = tk.Canvas(row, width=36, height=36,
                          bg=bg, highlightthickness=0)
        art_c.pack(side="left", padx=(8, 6), pady=3)
        self._draw_tiny_art(art_c, album.get("art"), 36)

        info = tk.Frame(row, bg=bg)
        info.pack(side="left", fill="x", expand=True)
        tk.Label(info, text=album.get("name", "?"),
                 font=FONT_BODY, fg=TEXT_PRIMARY,
                 bg=bg, anchor="w").pack(fill="x")
        tracks = album.get("tracks", [])
        tk.Label(info,
                 text=f"{len(tracks)} track{'s' if len(tracks)!=1 else ''}",
                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=bg,
                 anchor="w").pack(fill="x")

        tk.Button(row, text="▶",
                  font=FONT_BODY, fg=ACCENT, bg=bg,
                  relief="flat", bd=0, cursor="hand2", padx=8,
                  command=lambda a=album: self._on_album_click(a)
                  ).pack(side="right", padx=8)

        def _hi(e):
            row.config(bg=BG_CARD_HOVER)
            for w in row.winfo_children():
                try: w.config(bg=BG_CARD_HOVER)
                except Exception: pass

        def _lo(e):
            row.config(bg=bg)
            for w in row.winfo_children():
                try: w.config(bg=bg)
                except Exception: pass

        for w in [row] + list(row.winfo_children()):
            w.bind("<Enter>", _hi)
            w.bind("<Leave>", _lo)
            w.bind("<Button-1>", lambda e, a=album: self._on_album_click(a))

    def _show_tree_flat(self, albums):
        scroll = ScrollableFrame(self._content, bg=BG_DARK)
        scroll.pack(fill="both", expand=True)
        for alb in albums:
            self._tree_album_row(scroll.inner, alb)
        scroll.bind_all_children_scroll(scroll.inner)

    def _draw_tiny_art(self, canvas, art, size):
        from .image_loader import load_image_tk
        if art:
            photo = load_image_tk(art, size)
            if photo:
                canvas._photo = photo
                canvas.create_image(size // 2, size // 2,
                                    image=photo, anchor="center")
                return
        canvas.create_rectangle(0, 0, size, size,
                                fill=BG_PANEL, outline=BORDER)
        canvas.create_text(size // 2, size // 2,
                           text="♪", font=("Helvetica", 14),
                           fill=BORDER_ACCENT)

    # ------------------------------------------------------------------
    # Album Detail
    # ------------------------------------------------------------------

    def _on_album_click(self, album):
        self._current_album_view = album
        if self._config:
            self._config.set("last_album_key",    album.get("key", ""))
            self._config.set("last_album_letter", self._selected_letter)
        self._show_album_detail(album)

    def _show_album_detail(self, album):
        for w in self._content.winfo_children():
            w.destroy()

        main = tk.Frame(self._content, bg=BG_DARK)
        main.pack(fill="both", expand=True)

        tracks = album.get("tracks", [])
        sorted_tracks = sorted(
            tracks,
            key=lambda t: self._track_num(t.get("tracknumber", "0")))

        # ── Compact header ─────────────────────────────────────────────
        hdr = tk.Frame(main, bg=BG_PANEL)
        hdr.pack(fill="x")

        tk.Button(hdr, text="←",
                  font=FONT_BODY, fg=ACCENT, bg=BG_PANEL,
                  relief="flat", bd=0, cursor="hand2",
                  padx=6, pady=PAD_SM,
                  command=self._back_to_grid).pack(
            side="left", padx=(PAD, 0))

        # Normal-size album art (90x90)
        art_canvas = tk.Canvas(hdr, width=90, height=90,
                               bg=BG_PANEL, highlightthickness=0,
                               cursor="hand2")
        art_canvas.pack(side="left", padx=(0, PAD), pady=PAD_SM)
        self._draw_tiny_art(art_canvas, album.get("art"), 90)
        # Click art thumbnail to show full-size overlay
        art_canvas.bind("<Button-1>",
                        lambda e, a=album: self._show_art_overlay(a))

        # Buttons must be packed side="right" BEFORE the info frame
        # packs side="left", otherwise pack() pushes them off screen
        tk.Button(hdr, text="▶  Play All",
                  font=FONT_SUBHEAD, fg=BG_DARK, bg=ACCENT,
                  relief="flat", bd=0, cursor="hand2",
                  padx=16, pady=6,
                  command=lambda: self._play_all(sorted_tracks)).pack(
            side="right", padx=PAD)

        tk.Button(hdr, text="⇀ Shuffle",
                  font=FONT_SMALL, fg=ACCENT, bg=BG_PANEL,
                  relief="flat", bd=0, cursor="hand2",
                  padx=8, pady=6,
                  command=lambda: self._play_shuffle(sorted_tracks)).pack(
            side="right", padx=4)

        info = tk.Frame(hdr, bg=BG_PANEL)
        info.pack(side="left", fill="y", pady=PAD_SM)
        tk.Label(info, text=album.get("artist", "Unknown Artist"),
                 font=FONT_BODY, fg=ACCENT,
                 bg=BG_PANEL, anchor="w").pack(fill="x")
        tk.Label(info, text=album.get("name", "Unknown Album"),
                 font=FONT_BODY, fg=TEXT_PRIMARY,
                 bg=BG_PANEL, anchor="w").pack(fill="x")
        tk.Label(info, text=f"{len(tracks)} tracks",
                 font=FONT_SMALL, fg=TEXT_PRIMARY,
                 bg=BG_PANEL, anchor="w").pack(fill="x")

        # ── Track list ────────────────────────────────────────────────
        scroll = ScrollableFrame(main, bg=BG_DARK)
        scroll.pack(fill="both", expand=True)
        tk.Frame(scroll.inner, bg=BG_DARK, height=4).pack(fill="x")

        current = self._player.get_current_track()
        for i, track in enumerate(sorted_tracks):
            is_current = bool(current and
                              current.get("id") == track.get("id"))
            row = self._make_track_row(scroll.inner, track, i,
                                       sorted_tracks, is_current)
            row.pack(fill="x", padx=PAD_SM, pady=1)

        # Bind scroll on all rows and refresh scrollregion
        scroll.bind_all_children_scroll(scroll.inner)
        scroll.refresh_scrollregion()

    def _refresh_overlay_labels(self, album: dict, track: dict):
        """Update text on the overlay when track changes without rebuilding."""
        try:
            canvas = self._overlay_canvas
            # Remove old text items and redraw
            canvas.delete("overlaytext")
            w = canvas.winfo_width()
            h = canvas.winfo_height()
            try:
                nw_approx = canvas._fg_photo.width() if hasattr(
                    canvas, '_fg_photo') and canvas._fg_photo else int(w * 0.77)
            except Exception:
                nw_approx = int(w * 0.77)
            text_x = int(w * 0.03) + nw_approx + int(w * 0.04)
            text_y = h // 2 - 30
            canvas.create_text(text_x, text_y,
                               text=album.get("name", ""),
                               font=FONT_HEADING, fill=TEXT_PRIMARY,
                               anchor="w", width=w - text_x - 10,
                               tags="overlaytext")
            canvas.create_text(text_x, text_y + 30,
                               text=album.get("artist", ""),
                               font=FONT_BODY, fill=ACCENT,
                               anchor="w", width=w - text_x - 10,
                               tags="overlaytext")
            canvas.create_text(text_x, text_y + 60,
                               text=f"▶  {track.get('title','')}",
                               font=FONT_SMALL, fill=NEON_BLUE,
                               anchor="w", width=w - text_x - 10,
                               tags="overlaytext")
            canvas.create_text(text_x, h - 30,
                               text="Tap to close",
                               font=FONT_TINY, fill=TEXT_DIM,
                               anchor="w", tags="overlaytext")
        except Exception as e:
            print(f"[AlbumsPage] overlay label refresh error: {e}")

    def _show_art_overlay(self, album: dict):
        """Show album art scaled to 75% of content area with blurred background."""
        self._overlay_active = True
        for w in self._content.winfo_children():
            w.destroy()

        overlay = tk.Frame(self._content, bg="#000000")
        overlay.pack(fill="both", expand=True)

        # Canvas fills entire content area
        canvas = tk.Canvas(overlay, bg="#000000", highlightthickness=0)
        canvas.pack(fill="both", expand=True)

        self._overlay_canvas = canvas
        self._overlay_album  = album
        canvas.bind("<Configure>", self._on_overlay_resize)
        canvas.bind("<Button-1>",  self._close_art_overlay)

        # Close hint
        tk.Label(overlay, text="Tap anywhere to close",
                 font=FONT_TINY, fg=TEXT_PRIMARY, bg="#000000").pack(
            side="bottom", pady=4)

    def _on_overlay_resize(self, event):
        canvas = self._overlay_canvas
        album  = self._overlay_album
        w, h   = event.width, event.height
        if w < 2 or h < 2:
            return
        canvas.delete("all")

        art = album.get("art")
        if art:
            try:
                from PIL import Image, ImageTk, ImageFilter
                import io

                raw = io.BytesIO(art) if isinstance(art, (bytes, bytearray)) else art
                base_img = Image.open(raw).convert("RGB")

                # ── Background: 500% crop, blurred ───────────────────
                bg = base_img.copy()
                img_w, img_h = bg.size
                bg = bg.resize((img_w * 5, img_h * 5), Image.LANCZOS)
                bg_w, bg_h = bg.size
                left = max(0, (bg_w - w) // 2)
                top  = max(0, (bg_h - h) // 2)
                bg   = bg.crop((left, top,
                                left + min(w, bg_w),
                                top  + min(h, bg_h)))
                if bg.size != (w, h):
                    padded = Image.new("RGB", (w, h), (0, 0, 0))
                    padded.paste(bg, (0, 0))
                    bg = padded
                bg = bg.filter(ImageFilter.GaussianBlur(radius=18))
                from PIL import ImageEnhance
                bg = ImageEnhance.Brightness(bg).enhance(0.55)

                canvas._bg_photo = ImageTk.PhotoImage(bg)
                canvas.create_image(0, 0, image=canvas._bg_photo,
                                    anchor="nw")

                # ── Foreground: art at 75%, left-justified ────────────
                fg = base_img.copy()
                max_w = int(w * 0.75)
                max_h = int(h * 0.80)
                scale = min(max_w / fg.width, max_h / fg.height)
                nw = max(1, int(fg.width  * scale))
                nh = max(1, int(fg.height * scale))
                fg = fg.resize((nw, nh), Image.LANCZOS)

                # Left margin — 3% of width
                fg_x = int(w * 0.03)
                fg_y = (h - nh) // 2   # vertically centred

                # Drop shadow offset 6px right/down
                canvas.create_rectangle(fg_x + 6, fg_y + 6,
                                        fg_x + 6 + nw, fg_y + 6 + nh,
                                        fill="#000000", outline="",
                                        stipple="gray50")

                canvas._fg_photo = ImageTk.PhotoImage(fg)
                canvas.create_image(fg_x, fg_y,
                                    image=canvas._fg_photo,
                                    anchor="nw")

            except Exception as e:
                print(f"[AlbumsPage] overlay art error: {e}")
                # Plain black fallback
                canvas.create_rectangle(0, 0, w, h, fill="#000000",
                                        outline="")
        else:
            canvas.create_rectangle(0, 0, w, h, fill="#000000", outline="")

        # Album name + artist — to the right of the art
        # Calculate text x based on art width (3% margin + art width + gap)
        try:
            nw_approx = canvas._fg_photo.width() if hasattr(
                canvas, '_fg_photo') and canvas._fg_photo else int(w * 0.77)
        except Exception:
            nw_approx = int(w * 0.77)
        text_x = int(w * 0.03) + nw_approx + int(w * 0.04)
        text_y = h // 2 - 20

        canvas.create_text(text_x, text_y,
                           text=album.get("name", ""),
                           font=FONT_HEADING, fill=TEXT_PRIMARY,
                           anchor="w", width=w - text_x - 10)
        canvas.create_text(text_x, text_y + 30,
                           text=album.get("artist", ""),
                           font=FONT_BODY, fill=ACCENT,
                           anchor="w", width=w - text_x - 10)
        canvas.create_text(text_x, h - 30,
                           text="Tap to close",
                           font=FONT_TINY, fill=TEXT_DIM,
                           anchor="w")

    def _close_art_overlay(self, _event=None):
        """Return to album detail view."""
        self._overlay_active = False
        if self._current_album_view:
            self._show_album_detail(self._current_album_view)

    def _back_to_grid(self):
        self._overlay_active     = False
        self._current_album_view = None
        self._show_content()

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    def _play_all(self, tracks):
        if tracks:
            if self._pm:
                self._pm.set_album_context(tracks, 0)
            self._player.set_queue(tracks, 0)

    def _play_shuffle(self, tracks):
        import random
        if tracks:
            shuffled = list(tracks)
            random.shuffle(shuffled)
            if self._pm:
                self._pm.set_album_context(shuffled, 0)
            self._player.set_queue(shuffled, 0)

    def _play_from(self, tracks, index):
        if self._pm:
            self._pm.set_album_context(tracks, index)
        self._player.set_queue(tracks, index)
        if self._current_album_view:
            self._show_album_detail(self._current_album_view)

    def _add_to_queue(self, track):
        if not self._pm:
            return
        ok, msg = self._pm.add_selection(track)
        self._show_toast(msg, ok)

    def _show_toast(self, msg, success):
        try:
            self._app_ref.show_notification(
                msg, "success" if success else "warning")
        except Exception:
            pass

    def _make_track_row(self, parent, track, index, all_tracks, is_current):
        bg  = BG_PANEL if index % 2 == 0 else BG_CARD
        row = tk.Frame(parent, bg=bg)

        # Track number
        num = str(index + 1).zfill(2)
        tk.Label(row, text=num, font=FONT_SMALL,
                 fg=NEON_BLUE if is_current else TEXT_PRIMARY,
                 bg=bg, width=3).pack(side="left", padx=(8, 2))

        # Now-playing indicator
        if is_current:
            tk.Label(row, text="▶", font=FONT_SMALL,
                     fg=NEON_BLUE, bg=bg).pack(side="left")

        # ── 36px album art thumbnail ──────────────────────────────────
        # Find album dict from library for this track
        album_obj = None
        if self._current_album_view:
            album_obj = self._current_album_view
        art_c = make_track_art_canvas(row, album_obj, bg, size=36)
        art_c.pack(side="left", padx=(2, 6), pady=2)

        # Title
        tk.Label(row, text=track.get("title", "Unknown"),
                 font=FONT_TRACK,
                 fg=NEON_BLUE if is_current else TEXT_PRIMARY,
                 bg=bg, anchor="w").pack(
            side="left", fill="x", expand=True, padx=(0, 8))

        # Duration
        dur = self._fmt_duration(track.get("duration", 0))
        tk.Label(row, text=dur, font=FONT_SMALL,
                 fg=TEXT_PRIMARY, bg=bg).pack(side="right", padx=(4, 4))

        # ▶ play button — plays this track immediately
        play_btn = tk.Button(
            row, text="▶",
            font=("Helvetica", 13),
            fg=NEON_BLUE, bg=bg, relief="flat", bd=0,
            cursor="hand2", padx=6,
            command=lambda t=track, i=index: self._play_from(all_tracks, i))
        play_btn.pack(side="right", padx=(0, 2))
        play_btn.bind("<Enter>",
                      lambda e, b=play_btn: b.config(fg=TEXT_PRIMARY))
        play_btn.bind("<Leave>",
                      lambda e, b=play_btn: b.config(fg=NEON_BLUE))

        # + queue button
        if self._pm:
            add_btn = tk.Button(
                row, text="＋",
                font=("Helvetica", 13, "bold"),
                fg=ACCENT, bg=bg, relief="flat", bd=0,
                cursor="hand2", padx=6,
                command=lambda t=track: self._add_to_queue(t))
            add_btn.pack(side="right", padx=(0, 4))
            add_btn.bind("<Enter>",
                         lambda e, b=add_btn: b.config(fg=SUCCESS))
            add_btn.bind("<Leave>",
                         lambda e, b=add_btn: b.config(fg=ACCENT))

        play_fn = lambda e, t=track, i=index: self._play_from(all_tracks, i)

        def _hi(e, r=row):
            r.config(bg=BG_CARD_HOVER)
            for w in r.winfo_children():
                try: w.config(bg=BG_CARD_HOVER)
                except Exception: pass

        def _lo(e, r=row, b=bg):
            r.config(bg=b)
            for w in r.winfo_children():
                try: w.config(bg=b)
                except Exception: pass

        for w in [row] + list(row.winfo_children()):
            w.bind("<Enter>", _hi)
            w.bind("<Leave>", _lo)
            w.bind("<Double-Button-1>", play_fn)

        return row

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _clear_search(self):
        """Clear the search field and return focus to the content area."""
        self._search_var.set("")
        self.focus_set()

    def _clear_search_focus(self):
        """Dismiss keyboard without clearing."""
        self.focus_set()

    def _on_search(self, *_):
        q = self._search_var.get().strip()
        if not q:
            self._show_content()
            return
        results = self._library.search(q)
        self._show_content(results["albums"])

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _empty_label(self, text):
        tk.Label(self._content, text=text,
                 font=FONT_HEADING, fg=TEXT_PRIMARY, bg=BG_DARK).pack(
            expand=True)

    def _update_stats(self):
        s = self._library.get_stats()
        self._lbl_stats.config(
            text=f"{s['total_tracks']} tracks · "
                 f"{s['total_albums']} albums · "
                 f"{s['total_artists']} artists")

    @staticmethod
    def _fmt_duration(secs):
        m, s = divmod(int(secs), 60)
        return f"{m}:{s:02d}"

    @staticmethod
    def _track_num(tn):
        try:
            return int(str(tn).split("/")[0])
        except ValueError:
            return 0

    # ------------------------------------------------------------------
    # Public refresh — called once library scan completes
    # ------------------------------------------------------------------

    def refresh(self):
        """
        Called by pulsebox.py after the library has loaded, and again
        on every subsequent visit to the Albums page via show_page().
        First call: packs frames and renders the initial grid.
        Subsequent calls: rebuilds the A-Z bar only (alphabet may change
        after a rescan) but does NOT re-pack frames or re-render the grid
        — doing so caused duplicate album cards to appear.
        """
        first_load = not self._library_ready
        self._library_ready = True

        if first_load:
            # Remove loading placeholder on first load only
            self._loading_placeholder.pack_forget()

        # Restore browse mode
        if self._config:
            mode = self._config.get("browse_mode", "covers")
            self._browse_mode = mode
            self._mode_var.set(mode)

        # Pick starting letter
        # Always rebuild A-Z bar (alphabet may change after a rescan)
        # On first load: restore last used letter from config
        # On subsequent visits: keep the current _selected_letter as-is
        if first_load:
            available = set(self._library.get_alphabet_index())
            if self._config:
                last_letter = self._config.get("last_album_letter", "A")
            else:
                last_letter = "A"

            if last_letter in available or last_letter == "ALL":
                self._selected_letter = last_letter
            elif "A" in available:
                self._selected_letter = "A"
            else:
                self._selected_letter = "ALL"

        self._build_az_bar()

        if not first_load:
            # Subsequent visits: leave the current view unchanged
            return

        # First load only: pack frames and restore/show grid
        self._az_frame.pack(fill="x")
        self._content.pack(fill="both", expand=True)

        # Restore last played album or show grid
        if self._config:
            last_key = self._config.get("last_album_key", "")
            if last_key:
                for alb in self._library.get_all_albums():
                    if alb.get("key") == last_key:
                        self._current_album_view = alb
                        self._show_album_detail(alb)
                        return

        self._show_content()
