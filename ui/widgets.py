# ---------------------------------------------------------------------------
# ScrollableFrame
# ---------------------------------------------------------------------------

import tkinter as tk
from tkinter import ttk
from .theme import (ACCENT, ACCENT2, BG_CARD, BG_CARD_HOVER, BG_DARK, BG_PANEL,
                    BORDER, BORDER_ACCENT, FONT_BODY, FONT_SMALL, FONT_TINY,
                    FONT_TRACK_SM, TEXT_DIM, TEXT_PRIMARY, TEXT_SECONDARY,
                    NEON_BLUE, SUCCESS, DANGER, WARNING,
                    FONT_DISPLAY, FONT_HEADING, FONT_SUBHEAD,
                    ART_SIZE, CARD_W, PAD, PAD_SM, PLAYER_H)


class ScrollableFrame(tk.Frame):
    """
    Vertical scrollable frame using a Canvas.
    - Scrollbar always visible on right
    - Button-4/5 for Linux/Pi mousewheel
    - Touch drag scroll via B1-Motion on canvas and all children
    - Scrollregion updated reliably after all children render
    """

    def __init__(self, parent, bg=BG_DARK, **kwargs):
        super().__init__(parent, bg=bg, **kwargs)

        # Per-instance set of widget paths with scroll bound. Must be an
        # instance attribute (not class-level) — otherwise all
        # ScrollableFrame instances across every page share one set,
        # and identical widget paths (Tkinter reuses path names based on
        # creation order) from different pages can cause scroll bindings
        # to be silently skipped on a newly built page.
        self._scroll_bound = set()

        self._canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self._scrollbar = tk.Scrollbar(self, orient="vertical",
                                       command=self._canvas.yview,
                                       bg=BG_PANEL, troughcolor=BG_DARK,
                                       width=20)
        self.inner = tk.Frame(self._canvas, bg=bg)

        self._window_id = self._canvas.create_window(
            (0, 0), window=self.inner, anchor="nw")

        self._canvas.configure(yscrollcommand=self._scrollbar.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        self._scrollbar.pack(side="right", fill="y")

        # Update scrollregion whenever inner frame resizes
        self.inner.bind("<Configure>", self._update_scrollregion)
        self._canvas.bind("<Configure>", self._on_canvas_resize)

        # Mouse wheel — bind on canvas only
        self._canvas.bind("<Button-4>",  lambda e: self._scroll(-3))
        self._canvas.bind("<Button-5>",  lambda e: self._scroll(3))
        self._canvas.bind("<MouseWheel>",
                          lambda e: self._scroll(
                              -1 * (e.delta // 120) if e.delta else 0))

        # Touch drag tracking
        self._touch_y     = None
        self._touch_start = None
        self._touch_moved = False
        self._canvas.bind("<ButtonPress-1>",  self._touch_press)
        self._canvas.bind("<B1-Motion>",       self._touch_motion)
        self._canvas.bind("<ButtonRelease-1>", self._touch_release)

    def _update_scrollregion(self, _event=None):
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def viewport_width(self) -> int:
        """Usable inner width (excludes the scrollbar). 0 if not yet mapped."""
        try:
            w = self._canvas.winfo_width()
            return w if w and w > 1 else 0
        except Exception:
            return 0

    def _on_canvas_resize(self, event):
        self._canvas.itemconfig(self._window_id, width=event.width)

    def _scroll(self, units: int):
        if units:
            self._canvas.yview_scroll(units, "units")
            # Force the canvas to repaint newly-exposed widgets.
            # Fast wheel/touch scrolling can move many widgets into view
            # at once; their canvases (poster art, album art etc.) were
            # never painted while off-screen and don't receive an expose
            # event from yview_scroll alone — leaving them blank until
            # an unrelated event (mouse hover) forces a redraw.
            self._canvas.update_idletasks()

    def _touch_press(self, event):
        self._touch_y     = event.y_root
        self._touch_start = event.y_root
        self._touch_moved = False

    def _touch_motion(self, event):
        if self._touch_y is None:
            return
        dy = self._touch_y - event.y_root
        total = abs(event.y_root - self._touch_start)
        if total > 4:                          # was 8 — activate sooner
            self._touch_moved = True
        if self._touch_moved and abs(dy) > 1:  # was 2 — more responsive
            units = int(dy / 4)                # was 6 — scroll faster
            if units:
                self._canvas.yview_scroll(units, "units")
                # Same redraw fix as wheel scroll, see _scroll() above.
                self._canvas.update_idletasks()
            self._touch_y = event.y_root

    def _touch_release(self, event):
        self._touch_y     = None
        self._touch_moved = False

    def enable_scroll_for(self, widget):
        """
        Bind wheel and touch scroll on a child widget.
        Guards against duplicate bindings — calling this twice on the same
        widget is safe and will not stack handlers.
        """
        wid = str(widget)
        if wid in self._scroll_bound:
            return
        self._scroll_bound.add(wid)
        widget.bind("<Button-4>",      lambda e: self._scroll(-3))
        widget.bind("<Button-5>",      lambda e: self._scroll(3))
        widget.bind("<MouseWheel>",
                    lambda e: self._scroll(
                        -1 * (e.delta // 120) if e.delta else 0))
        widget.bind("<ButtonPress-1>",   self._touch_press,   add="+")
        widget.bind("<B1-Motion>",        self._touch_motion,  add="+")
        widget.bind("<ButtonRelease-1>", self._touch_release, add="+")

    def bind_all_children_scroll(self, widget):
        """Recursively enable scroll and touch on all children."""
        self.enable_scroll_for(widget)
        for child in widget.winfo_children():
            self.bind_all_children_scroll(child)

    def clear_scroll_bindings(self):
        """Call this when the scroll frame content is rebuilt."""
        self._scroll_bound = set()

    def refresh_scrollregion(self):
        self.after(10, self._update_scrollregion)

    def scroll_to_top(self):
        self._canvas.yview_moveto(0)

    def scroll_to_widget(self, widget):
        """Scroll so that widget is visible."""
        self._canvas.update_idletasks()
        try:
            wy = widget.winfo_y()
            wh = widget.winfo_height()
            w = widget.master
            while w is not None and w is not self.inner:
                wy += w.winfo_y()
                w   = w.master
            canvas_h = self._canvas.winfo_height()
            total_h  = self.inner.winfo_height()
            if total_h <= canvas_h:
                return
            top_frac = self._canvas.yview()[0]
            top_px   = top_frac * total_h
            margin   = 20
            if wy + wh > top_px + canvas_h:
                target_px = wy + wh - canvas_h + margin
            else:
                target_px = wy - margin
            target_frac = max(0.0, min(1.0, target_px / total_h))
            self._canvas.yview_moveto(target_frac)
        except Exception:
            pass

    def bind_focus_scroll(self, widget):
        """Recursively bind FocusIn scroll."""
        widget.bind("<FocusIn>",
                    lambda e: self.scroll_to_widget(e.widget), add="+")
        for child in widget.winfo_children():
            self.bind_focus_scroll(child)


# ---------------------------------------------------------------------------
# Album Card
# ---------------------------------------------------------------------------

class AlbumCard(tk.Frame):
    """Clickable album tile with art thumbnail and metadata."""

    def __init__(self, parent, album: dict, on_click=None, **kwargs):
        super().__init__(parent, bg=BG_CARD, cursor="hand2",
                         padx=6, pady=6, **kwargs)
        self._album    = album
        self._on_click = on_click
        self._photo    = None
        self._build()

    def _build(self):
        # Art canvas
        self._art_canvas = tk.Canvas(
            self, width=ART_SIZE, height=ART_SIZE,
            bg=BG_PANEL, highlightthickness=0)
        self._art_canvas.pack()

        # Artist
        tk.Label(self, text=self._album.get("artist", "Unknown"),
                 font=FONT_SMALL, fg=ACCENT, bg=BG_CARD,
                 wraplength=CARD_W - 16, anchor="w").pack(
            fill="x", padx=4, pady=(4, 0))

        # Album name
        tk.Label(self, text=self._album.get("name", "Unknown Album"),
                 font=FONT_TRACK_SM, fg=TEXT_PRIMARY, bg=BG_CARD,
                 wraplength=CARD_W - 16, anchor="w",
                 justify="left").pack(fill="x", padx=4)

        # Track count
        n = len(self._album.get("tracks", []))
        tk.Label(self, text=f"{n} track{'s' if n != 1 else ''}",
                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_CARD).pack(
            padx=4, pady=(0, 4))

        # Bind interactions — click only on outer frame to prevent
        # multiple _click firings (one per child widget) on a single tap
        self.bind("<Enter>",    self._hover_on)
        self.bind("<Leave>",    self._hover_off)
        self.bind("<Button-1>", self._click)
        for w in self.winfo_children():
            w.bind("<Enter>",    self._hover_on)
            w.bind("<Leave>",    self._hover_off)
            # Propagate click to parent frame rather than binding directly
            w.bind("<Button-1>", lambda e: self._click())

        # Defer art load until after widget is mapped
        self.after(5, self._load_art)

    def _load_art(self):
        from .image_loader import load_image_tk
        art = self._album.get("art")
        if art:
            photo = load_image_tk(art, ART_SIZE)
            if photo:
                self._photo = photo
                self._art_canvas.delete("all")
                self._art_canvas.create_image(
                    ART_SIZE // 2, ART_SIZE // 2,
                    image=self._photo, anchor="center")
                return
        # Placeholder
        self._art_canvas.create_rectangle(
            0, 0, ART_SIZE, ART_SIZE, fill=BG_PANEL, outline=BORDER)
        self._art_canvas.create_text(
            ART_SIZE // 2, ART_SIZE // 2,
            text="♪", font=("Helvetica", 48), fill=BORDER_ACCENT)

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
            self._on_click(self._album)


# ---------------------------------------------------------------------------
# Track art helper
# ---------------------------------------------------------------------------

def make_track_art_canvas(parent, album: dict | None, bg: str,
                          size: int = 36) -> tk.Canvas:
    """
    Return a (size x size) Canvas with the album art drawn on it.
    Art loads deferred so the widget is mapped before PIL renders.
    """
    canvas = tk.Canvas(parent, width=size, height=size,
                       bg=bg, highlightthickness=0)

    def _draw():
        from .image_loader import load_image_tk
        art = album.get("art") if album else None
        if art:
            photo = load_image_tk(art, size)
            if photo:
                canvas._photo = photo
                canvas.delete("all")
                canvas.create_image(size // 2, size // 2,
                                    image=photo, anchor="center")
                return
        canvas.create_rectangle(0, 0, size, size,
                                fill=BG_PANEL, outline=BORDER)
        canvas.create_text(size // 2, size // 2,
                           text="♪", font=("Helvetica", 14),
                           fill=BORDER_ACCENT)

    canvas.after(5, _draw)
    return canvas


# ---------------------------------------------------------------------------
# Player Bar
# ---------------------------------------------------------------------------

class PlayerBar(tk.Frame):
    """Persistent bottom player bar — always visible."""

    def __init__(self, parent, player, **kwargs):
        super().__init__(parent, bg=BG_PANEL, height=PLAYER_H,
                         bd=0, relief="flat", **kwargs)
        self._player        = player
        self._seek_dragging = False
        self._build()
        self._tick()

    def _build(self):
        self.pack_propagate(False)
        # Top accent line — 3px so it stays visible even with 1px offset
        tk.Frame(self, bg=ACCENT, height=3).pack(fill="x")

        main = tk.Frame(self, bg=BG_PANEL)
        main.pack(fill="both", expand=True, padx=12, pady=2)

        # ── Left: track info ──────────────────────────────────────────
        left = tk.Frame(main, bg=BG_PANEL, width=280)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        self._lbl_title = tk.Label(
            left, text="No track playing",
            font=FONT_SUBHEAD, fg=TEXT_PRIMARY,
            bg=BG_PANEL, anchor="w")
        self._lbl_title.pack(fill="x")

        self._lbl_artist = tk.Label(
            left, text="—", font=FONT_SMALL,
            fg=ACCENT, bg=BG_PANEL, anchor="w")
        self._lbl_artist.pack(fill="x")

        self._lbl_album = tk.Label(
            left, text="", font=FONT_TINY,
            fg=TEXT_PRIMARY, bg=BG_PANEL, anchor="w")
        self._lbl_album.pack(fill="x")

        # ── Centre: transport + seek ──────────────────────────────────
        centre = tk.Frame(main, bg=BG_PANEL)
        centre.pack(side="left", fill="both", expand=True, padx=20)

        btns = tk.Frame(centre, bg=BG_PANEL)
        btns.pack(pady=(4, 2))

        bcfg = dict(font=("Helvetica", 18), bg=BG_PANEL,
                    relief="flat", bd=0, cursor="hand2",
                    activebackground=BG_PANEL)

        tk.Button(btns, text="⏮", fg=TEXT_PRIMARY,
                  command=self._player.previous_track,
                  **bcfg).pack(side="left", padx=4)

        self._btn_play = tk.Button(
            btns, text="▶", fg=ACCENT, width=2,
            command=self._toggle_play, **bcfg)
        self._btn_play.pack(side="left", padx=4)

        tk.Button(btns, text="⏭", fg=TEXT_PRIMARY,
                  command=self._player.next_track,
                  **bcfg).pack(side="left", padx=4)

        self._btn_stop = tk.Button(btns, text="⏹", fg=TEXT_PRIMARY,
                  command=self._player.stop,
                  **bcfg)
        self._btn_stop.pack(side="left", padx=12)

        # Seek bar
        seek_row = tk.Frame(centre, bg=BG_PANEL)
        seek_row.pack(fill="x")

        self._lbl_pos = tk.Label(seek_row, text="0:00",
                                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_PANEL)
        self._lbl_pos.pack(side="left")

        self._seek_var = tk.DoubleVar()
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("PB.Horizontal.TScale",
                        background=BG_PANEL, troughcolor=BG_DARK,
                        sliderlength=14, sliderrelief="flat")

        self._seek = ttk.Scale(seek_row, from_=0, to=100,
                               variable=self._seek_var,
                               orient="horizontal",
                               style="PB.Horizontal.TScale",
                               command=self._on_seek_move)
        self._seek.pack(side="left", fill="x", expand=True, padx=6)
        self._seek.bind("<ButtonPress-1>",  self._on_seek_press)
        self._seek.bind("<B1-Motion>",        self._on_seek_drag)
        self._seek.bind("<ButtonRelease-1>", self._on_seek_release)

        self._lbl_dur = tk.Label(seek_row, text="0:00",
                                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_PANEL)
        self._lbl_dur.pack(side="left")

        # ── Right: volume slider ──────────────────────────────────────
        right = tk.Frame(main, bg=BG_PANEL, width=180)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)

        vol_row = tk.Frame(right, bg=BG_PANEL)
        vol_row.pack(anchor="center", expand=True)

        tk.Label(vol_row, text="🔊", font=("Helvetica", 13),
                 fg=TEXT_PRIMARY, bg=BG_PANEL).pack(side="left")

        self._vol_var = tk.IntVar(value=self._player.get_volume())
        self._syncing_vol = False   # guards against feedback when syncing
        ttk.Scale(vol_row, from_=0, to=100,
                  variable=self._vol_var,
                  orient="horizontal", length=120,
                  style="PB.Horizontal.TScale",
                  command=self._on_vol_change).pack(side="left", padx=4)

        self._lbl_vol = tk.Label(vol_row,
                                 text=f"{self._player.get_volume()}%",
                                 font=FONT_TINY, fg=TEXT_PRIMARY,
                                 bg=BG_PANEL, width=4)
        self._lbl_vol.pack(side="left")

    # ------------------------------------------------------------------

    def _toggle_play(self):
        from core.player import PlayerState
        if self._player.get_state() == PlayerState.PLAYING:
            self._player.pause()
        else:
            self._player.play()

    def _on_vol_change(self, _v=None):
        # Ignore programmatic updates (from update_volume) so syncing the
        # slider to an external change doesn't loop back into set_volume.
        if self._syncing_vol:
            return
        vol = int(self._vol_var.get())
        self._player.set_volume(vol)
        self._lbl_vol.config(text=f"{vol}%")

    def update_volume(self, vol: int):
        """Reflect an externally-changed volume on the slider and label.

        Safe to call for changes made by the rotary encoder, mute/reset,
        the web remote, etc. Does NOT call set_volume (the change already
        happened) and won't retrigger _on_vol_change. Must run on the main
        thread — background callers should marshal via root.after().
        """
        try:
            vol = max(0, min(100, int(vol)))
            if int(self._vol_var.get()) == vol:
                return
            self._syncing_vol = True
            self._vol_var.set(vol)
            self._lbl_vol.config(text=f"{vol}%")
        except Exception:
            pass
        finally:
            self._syncing_vol = False

    def set_stop_command(self, cmd):
        """Override the Stop button command."""
        if hasattr(self, "_btn_stop"):
            self._btn_stop.config(command=cmd)
            return
        for w in self.winfo_children():
            self._find_and_set_stop(w, cmd)

    def _find_and_set_stop(self, widget, cmd):
        if isinstance(widget, tk.Button):
            try:
                if widget.cget('text') == '⏹':
                    widget.config(command=cmd)
                    return
            except Exception:
                pass
        for child in widget.winfo_children():
            self._find_and_set_stop(child, cmd)

    def _on_seek_move(self, _v=None):
        pass  # visual update only — commit on release

    def _on_seek_press(self, event):
        self._seek_dragging  = True
        self._seek_touch_x   = event.x_root
        self._seek_touch_val = self._seek_var.get()

    def _on_seek_drag(self, event):
        """Handle touch drag on seek bar — move proportionally."""
        if not self._seek_dragging:
            return
        try:
            # Calculate position from click x relative to slider width
            slider_w = self._seek.winfo_width()
            if slider_w < 2:
                return
            # x relative to slider widget
            x = event.x
            pct = max(0.0, min(100.0, (x / slider_w) * 100))
            self._seek_var.set(pct)
            # Update position label
            dur = self._player.get_duration()
            if dur > 0:
                pos = (pct / 100) * dur
                self._lbl_pos.config(text=self._fmt(pos))
        except Exception:
            pass

    def _on_seek_release(self, _e=None):
        self._seek_dragging = False
        dur = self._player.get_duration()
        if dur > 0:
            self._player.seek((self._seek_var.get() / 100) * dur)

    def clear(self):
        """Reset player bar to stopped/empty state."""
        self.update_track(None)
        self._seek_var.set(0)
        self._lbl_pos.config(text="0:00")
        self._lbl_dur.config(text="0:00")
        self._btn_play.config(text="▶")

    def update_track(self, track):
        if track:
            self._lbl_title.config(text=track.get("title", "Unknown"))
            self._lbl_artist.config(text=track.get("artist", ""))
            self._lbl_album.config(text=track.get("album", ""))
        else:
            self._lbl_title.config(text="No track playing")
            self._lbl_artist.config(text="—")
            self._lbl_album.config(text="")

    def update_state(self, state):
        from core.player import PlayerState
        self._btn_play.config(
            text="⏸" if state == PlayerState.PLAYING else "▶")

    def update_position(self, pos, dur):
        if self._seek_dragging:
            return
        self._seek_var.set((pos / dur * 100) if dur > 0 else 0)
        self._lbl_pos.config(text=self._fmt(pos))
        self._lbl_dur.config(text=self._fmt(dur))

    @staticmethod
    def _fmt(s):
        m, sec = divmod(int(s), 60)
        return f"{m}:{sec:02d}"

    # ------------------------------------------------------------------
    # Video mode — hijack the bar for video playback controls
    # ------------------------------------------------------------------

    def enter_video_mode(self, title: str,
                         on_pause,
                         on_restart,
                         on_stop):
        """
        Switch the PlayerBar to video mode.
        - Title area shows the movie name
        - Play/Pause button controls the video
        - ⏮ restarts from the beginning
        - ⏹ calls on_stop (returns to library)
        - Seek bar is driven externally via update_video_position()
        - Volume remains active
        """
        self._video_mode     = True
        self._video_on_pause = on_pause
        self._video_on_stop  = on_stop

        # Show movie title in the info area
        self._lbl_title.config(text=title)
        self._lbl_artist.config(text="▶  Now Playing")
        self._lbl_album.config(text="Touch seek bar to scrub")

        # Rewire transport buttons
        self._btn_play.config(text="⏸", fg=ACCENT,
                              command=self._video_pause_toggle)

        # Rewire ⏮ to restart, ⏹ to return to library
        for w in self._btn_play.master.winfo_children():
            t = w.cget("text") if isinstance(w, tk.Button) else ""
            if t == "⏮":
                w.config(command=on_restart)
            elif t == "⏹":
                w.config(command=on_stop, fg=DANGER)

        # Reset seek bar
        self._seek_var.set(0)
        self._lbl_pos.config(text="0:00")
        self._lbl_dur.config(text="0:00")

        # Rewire seek release to video seek
        self._seek.bind("<ButtonRelease-1>", self._video_seek_release)

    def exit_video_mode(self):
        """Restore the PlayerBar to music mode."""
        self._video_mode = False
        self._video_on_pause = None
        self._video_on_stop  = None

        # Restore play/pause button
        self._btn_play.config(command=self._toggle_play, fg=ACCENT)

        # Restore ⏮ and ⏹
        for w in self._btn_play.master.winfo_children():
            t = w.cget("text") if isinstance(w, tk.Button) else ""
            if t == "⏮":
                w.config(command=self._player.previous_track)
            elif t == "⏹":
                w.config(command=self._player.stop, fg=TEXT_PRIMARY)

        # Restore seek release
        self._seek.bind("<ButtonRelease-1>", self._on_seek_release)

        # Refresh from player state
        self.update_track(self._player.get_current_track())

    def update_video_position(self, pos: float, dur: float, paused: bool):
        """Called by the video player every second to update the bar."""
        if not getattr(self, "_video_mode", False):
            return
        if not self._seek_dragging:
            self._seek_var.set((pos / dur * 100) if dur > 0 else 0)
            self._lbl_pos.config(text=self._fmt(pos))
            self._lbl_dur.config(text=self._fmt(dur))
        self._btn_play.config(text="▶" if paused else "⏸")

    def _video_pause_toggle(self):
        if self._video_on_pause:
            self._video_on_pause()

    def _video_seek_release(self, _e=None):
        """Seek within the video on slider release."""
        if hasattr(self, "_video_seek_cb") and self._video_seek_cb:
            self._video_seek_cb(self._seek_var.get())

    def set_video_seek_callback(self, cb):
        """Register a callback(pct 0-100) for video seeking."""
        self._video_seek_cb = cb

    def _tick(self):
        from core.player import PlayerState
        # In video mode the tick is driven by the video player
        if getattr(self, "_video_mode", False):
            self.after(500, self._tick)
            return
        state = self._player.get_state()
        self.update_state(state)
        self.update_track(self._player.get_current_track())
        if state == PlayerState.PLAYING:
            self.update_position(
                self._player.get_position(),
                self._player.get_duration())
        # Keep the volume slider in sync with the player's volume, so changes
        # made by the rotary encoder, mute/reset or the web remote show up even
        # if they didn't come from dragging the slider.
        try:
            self.update_volume(self._player.get_volume())
        except Exception:
            pass
        self.after(500, self._tick)
