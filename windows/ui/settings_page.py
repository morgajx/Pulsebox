"""
PulseBox Settings Page
=======================
Settings grouped into collapsible cards — same pattern as the Modules page.
Click a section header or the + button to expand.  All settings and save
behaviour are identical to the previous flat layout.
"""

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .theme import fs, px
from .theme import (ACCENT, ACCENT2, BG_CARD, BG_DARK, BG_PANEL,
                    BORDER, DANGER, FONT_BODY, FONT_DISPLAY, FONT_HEADING,
                    FONT_SMALL, FONT_SUBHEAD, FONT_TINY, NEON_BLUE, PAD,
                    PAD_SM, SUCCESS, TEXT_DIM, TEXT_PRIMARY, TEXT_SECONDARY,
                    WARNING)
from .widgets import ScrollableFrame
from core.config import APP_VERSION
from core import winplat


# ---------------------------------------------------------------------------
# Colour cycle — one per section card (mirrors modules page)
# ---------------------------------------------------------------------------
_SECTION_COLOURS = [
    "#e8b004",  # amber      — Music Library
    "#00d4ff",  # cyan       — Video Library
    "#4488ff",  # blue       — Network Drives
    "#00cc88",  # green      — Playback
    "#cc44ff",  # purple     — Display
    "#aaaaaa",  # grey       — About
]


# ---------------------------------------------------------------------------
# Windows build: the Pi's Wi-Fi and Screen Rotation sections are gone (Windows
# Settings owns both), and network shares are mapped with `net use` through
# core.winplat instead of the sudo mount helper.
# ---------------------------------------------------------------------------

_AUDIO_DEFAULT_LABEL = "Windows default device"


# ---------------------------------------------------------------------------
# Collapsible section card  (mirrors _ModuleCard from modules_page)
# ---------------------------------------------------------------------------

class _SettingsCard(tk.Frame):
    """
    Collapsible card for a settings section.

    Collapsed:  ╔══════════════════════════════════╗
                ║  ⊞  Section Title           [+]  ║
                ╚══════════════════════════════════╝

    Expanded:   ╔══════════════════════════════════╗
                ║  ⊟  Section Title           [−]  ║
                ║──────────────────────────────────║
                ║  [section content]               ║
                ╚══════════════════════════════════╝
    """

    def __init__(self, parent, title: str, accent: str,
                 start_expanded: bool = False, scroll_ref=None, **kwargs):
        super().__init__(parent, bg=BG_PANEL,
                         highlightthickness=1,
                         highlightbackground=BORDER, **kwargs)
        self._accent      = accent
        self._expanded    = False
        self._scroll_ref  = scroll_ref
        self._build_header(title)
        self._build_body()
        if start_expanded:
            self._set_expanded(True)

    def _build_header(self, title: str):
        hdr = tk.Frame(self, bg=BG_PANEL, cursor="hand2")
        hdr.pack(fill="x")

        # Left accent stripe
        tk.Frame(hdr, bg=self._accent, width=4).pack(side="left", fill="y")

        # Toggle icon
        self._icon = tk.Label(hdr, text="⊞",
                              font=("Helvetica", fs(16), "bold"),
                              fg=self._accent, bg=BG_PANEL,
                              padx=8, pady=10)
        self._icon.pack(side="left")

        tk.Label(hdr, text=title,
                 font=FONT_SUBHEAD, fg=TEXT_PRIMARY,
                 bg=BG_PANEL, pady=10).pack(side="left")

        self._btn = tk.Button(hdr, text=" + ",
                              font=("Helvetica", fs(14), "bold"),
                              fg=self._accent, bg=BG_PANEL,
                              relief="flat", bd=0, cursor="hand2",
                              padx=14, pady=8,
                              activeforeground=TEXT_PRIMARY,
                              activebackground=BG_CARD,
                              command=self._on_toggle)
        self._btn.pack(side="right")

        for w in [hdr, self._icon]:
            w.bind("<Button-1>", lambda _e: self._on_toggle())

    def _build_body(self):
        self._body = tk.Frame(self, bg=BG_CARD)
        # Not packed until expanded

        # Accent separator line
        tk.Frame(self._body, bg=self._accent, height=2).pack(fill="x")

        # Inner content frame — callers pack widgets into this
        self.inner = tk.Frame(self._body, bg=BG_CARD, padx=PAD, pady=PAD_SM)
        self.inner.pack(fill="both", expand=True)

    def _rebind_and_scroll(self):
        if self._scroll_ref:
            self._scroll_ref.bind_all_children_scroll(self._body)
            self._scroll_ref.refresh_scrollregion()
            self._scroll_ref.scroll_to_widget(self)

    def _on_toggle(self):
        self._set_expanded(not self._expanded)

    def _set_expanded(self, expanded: bool):
        self._expanded = expanded
        if expanded:
            self._body.pack(fill="x", after=self._body.master.winfo_children()[0])
            self._btn.config(text=" − ")
            self._icon.config(text="⊟")
            self.config(highlightbackground=self._accent)
            self.after(60, self._rebind_and_scroll)
            self.after(50, self._scroll_into_view)
        else:
            self._body.pack_forget()
            self._btn.config(text=" + ")
            self._icon.config(text="⊞")
            self.config(highlightbackground=BORDER)

    def _scroll_into_view(self):
        from .widgets import ScrollableFrame
        w = self.master
        while w is not None:
            if isinstance(w, ScrollableFrame):
                w.scroll_to_widget(self)
                return
            w = getattr(w, "master", None)


# ---------------------------------------------------------------------------
# Settings Page
# ---------------------------------------------------------------------------

class SettingsPage(tk.Frame):

    def __init__(self, parent, config, library, player,
                 on_rescan=None, **kwargs):
        super().__init__(parent, bg=BG_DARK, **kwargs)
        self._config    = config
        self._library   = library
        self._player    = player
        self._on_rescan = on_rescan
        self._vars: dict[str, tk.Variable] = {}

        # Network drives
        self._net_drives: list[tuple[str,str]] = []
        self._net_drive_var   = tk.StringVar()
        self._net_combo: ttk.Combobox | None = None
        self._smb_share_var   = tk.StringVar()
        self._smb_user_var    = tk.StringVar()
        self._smb_pass_var    = tk.StringVar()
        self._smb_persist_var = tk.BooleanVar(value=True)
        self._smb_status_var  = tk.StringVar()

        # Audio output device (label shown -> VLC device id)
        self._audio_devices: list[tuple[str,str]] = []
        self._audio_dev_var   = tk.StringVar(value=_AUDIO_DEFAULT_LABEL)
        self._audio_combo: ttk.Combobox | None = None

        self._build()
        self._async_load_drives()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self):
        scroll = ScrollableFrame(self, bg=BG_DARK)
        scroll.pack(fill="both", expand=True)
        self._scroll_ref = scroll
        p = scroll.inner

        # Header
        hdr = tk.Frame(p, bg=BG_DARK)
        hdr.pack(fill="x", padx=PAD, pady=(PAD, 4))
        tk.Label(hdr, text="SETTINGS", font=FONT_DISPLAY,
                 fg=ACCENT, bg=BG_DARK).pack(side="left")
        tk.Button(hdr, text="Expand all",
                  font=FONT_TINY, fg=ACCENT, bg=BG_CARD,
                  relief="flat", bd=0, cursor="hand2",
                  padx=10, pady=4,
                  command=lambda: self._expand_all(True)).pack(side="right", padx=4)
        tk.Button(hdr, text="Collapse all",
                  font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_CARD,
                  relief="flat", bd=0, cursor="hand2",
                  padx=10, pady=4,
                  command=lambda: self._expand_all(False)).pack(side="right", padx=4)

        tk.Label(p, text="Click a section to expand its settings.",
                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_DARK).pack(
            anchor="w", padx=PAD, pady=(0, PAD_SM))

        self._cards: list[_SettingsCard] = []
        colours = _SECTION_COLOURS

        def _card(title, idx, build_fn, expanded=False):
            c = _SettingsCard(p, title, colours[idx % len(colours)],
                              start_expanded=expanded,
                              scroll_ref=scroll)
            c.pack(fill="x", padx=PAD, pady=(0, PAD_SM))
            build_fn(c.inner)
            self._cards.append(c)

        _card("Music Library",    0, self._build_library,    expanded=True)
        _card("Video Library",    1, self._build_video_dir)
        _card("Network Drives",   2, self._build_netdrive)
        _card("Playback",         3, self._build_playback)
        _card("Display",          4, self._build_display)
        _card("About",            5, self._build_about)

        # Save button
        tk.Frame(p, bg=BG_DARK, height=PAD_SM).pack()
        tk.Button(p, text="  Save Settings  ",
                  font=FONT_SUBHEAD, fg=BG_DARK, bg=ACCENT,
                  relief="flat", bd=0, cursor="hand2",
                  padx=20, pady=10,
                  command=self._save).pack(anchor="w", padx=PAD, pady=PAD)

        scroll.bind_all_children_scroll(p)
        scroll.refresh_scrollregion()

    def _expand_all(self, state: bool):
        for card in self._cards:
            card._set_expanded(state)

    # ------------------------------------------------------------------
    # Row helpers
    # ------------------------------------------------------------------

    def _row(self, parent, label: str = "", height=None):
        """Return a row Frame inside the card's inner frame."""
        r = tk.Frame(parent, bg=BG_CARD)
        if height:
            r.config(height=height)
        r.pack(fill="x", pady=2)
        if label:
            tk.Label(r, text=label, font=FONT_BODY, fg=TEXT_PRIMARY,
                     bg=BG_CARD, width=22, anchor="w").pack(side="left")
        return r

    def _add_slider(self, parent, key, label, lo, hi):
        r = self._row(parent, label)
        v = tk.IntVar(value=self._config.get(key))
        self._vars[key] = v
        val_lbl = tk.Label(r, textvariable=v, font=FONT_SMALL,
                           fg=ACCENT, bg=BG_CARD, width=4)
        val_lbl.pack(side="right")
        sl = ttk.Scale(r, from_=lo, to=hi, variable=v,
                       orient="horizontal", style="Jukebox.Horizontal.TScale")
        sl.pack(side="left", fill="x", expand=True, padx=(0, 8))

    def _add_toggle(self, parent, key, label):
        r = self._row(parent)
        v = tk.BooleanVar(value=bool(self._config.get(key)))
        self._vars[key] = v
        tk.Checkbutton(r, text=label, variable=v,
                       font=FONT_BODY, fg=TEXT_PRIMARY,
                       bg=BG_CARD, activebackground=BG_CARD,
                       activeforeground=TEXT_PRIMARY,   # keep text white when active/selected
                       selectcolor=BG_PANEL, cursor="hand2").pack(side="left")

    def _add_choice(self, parent, key, label, options):
        r = self._row(parent, label)
        v = tk.StringVar(value=self._config.get(key))
        self._vars[key] = v
        for opt in options:
            tk.Radiobutton(r, text=opt.capitalize(), variable=v, value=opt,
                           font=FONT_SMALL, fg=TEXT_PRIMARY,
                           bg=BG_CARD, activebackground=BG_CARD,
                           activeforeground=TEXT_PRIMARY,   # keep text white when active/selected
                           selectcolor=BG_PANEL, cursor="hand2").pack(
                side="left", padx=8)

    def _browse_dir(self, var):
        d = filedialog.askdirectory(title="Select Folder",
                                    initialdir=var.get())
        if d:
            var.set(d)

    # ------------------------------------------------------------------
    # Music Library
    # ------------------------------------------------------------------

    def _build_library(self, p):
        r1 = self._row(p, "Music Directory")
        v = tk.StringVar(value=self._config.get("music_dir"))
        self._vars["music_dir"] = v
        tk.Label(r1, textvariable=v, font=FONT_SMALL, fg=TEXT_PRIMARY,
                 bg=BG_CARD).pack(side="left", fill="x", expand=True, padx=(0, 8))
        tk.Button(r1, text="Browse…", font=FONT_SMALL, fg=ACCENT, bg=BG_PANEL,
                  relief="flat", bd=0, cursor="hand2", padx=8, pady=4,
                  command=lambda: self._browse_dir(v)).pack(side="right")

        r2 = self._row(p, "Use Network Drive")
        tk.Button(r2, text="Use Network Drive →",
                  font=FONT_SMALL, fg=NEON_BLUE, bg=BG_PANEL,
                  relief="flat", bd=0, cursor="hand2", padx=8, pady=4,
                  command=self._apply_net_drive_to_music_dir).pack(side="right")

        info_row = tk.Frame(p, bg=BG_CARD)
        info_row.pack(fill="x", pady=(4, 2))
        self._lbl_library = tk.Label(info_row, font=FONT_SMALL,
                                     fg=TEXT_PRIMARY, bg=BG_CARD)
        self._lbl_library.pack(side="left")
        self._update_library_label()
        tk.Label(info_row, text=f"DB: {self._library.get_db_path()}",
                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_CARD).pack(side="left", padx=8)
        tk.Button(info_row, text="Rescan Library",
                  font=FONT_SMALL, fg=BG_DARK, bg=NEON_BLUE,
                  relief="flat", bd=0, cursor="hand2", padx=12, pady=6,
                  command=self._rescan).pack(side="right")

    def refresh(self):
        """Called by show_page() each time settings tab is made visible."""
        self._update_library_label()

    def _update_library_label(self):
        s = self._library.get_stats()
        self._lbl_library.config(
            text=(f"{s['total_tracks']} tracks · "
                  f"{s['total_albums']} albums · "
                  f"{s['total_artists']} artists"))

    def _rescan(self):
        self._save(quiet=True)
        if self._on_rescan:
            self._on_rescan()
        self.after(1800, self._update_library_label)

    # ------------------------------------------------------------------
    # Video Library
    # ------------------------------------------------------------------

    def _build_video_dir(self, p):
        r1 = self._row(p, "Video Directory")
        v = tk.StringVar(value=self._config.get(
            "dvd_output_dir", os.path.expanduser("~/Videos")))
        self._vars["dvd_output_dir"] = v
        tk.Label(r1, textvariable=v, font=FONT_SMALL, fg=TEXT_PRIMARY,
                 bg=BG_CARD).pack(side="left", fill="x", expand=True, padx=(0, 8))
        tk.Button(r1, text="Browse…", font=FONT_SMALL, fg=ACCENT, bg=BG_PANEL,
                  relief="flat", bd=0, cursor="hand2", padx=8, pady=4,
                  command=lambda: self._browse_dir(v)).pack(side="right")

        tk.Label(p, text="This is also the DVD Ripper output directory.",
                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_CARD).pack(
            anchor="w", pady=(2, 0))

    # ------------------------------------------------------------------
    # Network Drives
    # ------------------------------------------------------------------

    def _build_netdrive(self, p):
        r = self._row(p, "Mapped Drive")
        self._net_combo = ttk.Combobox(r, textvariable=self._net_drive_var,
                                       state="readonly", font=FONT_BODY, width=30)
        self._net_combo.pack(side="left", padx=(0, 8), ipady=3)
        self._net_combo["values"] = ["Scanning…"]
        tk.Button(r, text="⟳ Refresh", font=FONT_SMALL,
                  fg=ACCENT, bg=BG_PANEL, relief="flat", bd=0,
                  cursor="hand2", padx=8, pady=4,
                  command=self._async_load_drives).pack(side="left")
        tk.Button(r, text="Disconnect", font=FONT_SMALL,
                  fg=ACCENT, bg=BG_PANEL, relief="flat", bd=0,
                  cursor="hand2", padx=8, pady=4,
                  command=self._unmount_selected_drive).pack(side="left")

        tk.Label(p, text='Select a drive then click "Use Network Drive →" in Music Library.',
                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_CARD,
                 wraplength=580, anchor="w", justify="left").pack(
            fill="x", pady=(4, 8))

        # ---- Add a NAS / SMB share -------------------------------------
        tk.Label(p, text="Add a NAS / SMB share", font=FONT_SMALL,
                 fg=ACCENT, bg=BG_CARD, anchor="w").pack(fill="x", pady=(4, 2))

        def _ent(row_label, var, show=None, width=30):
            rr = self._row(p, row_label)
            e = tk.Entry(rr, textvariable=var, font=FONT_BODY, bg=BG_PANEL,
                         fg=TEXT_PRIMARY, insertbackground=ACCENT,
                         relief="flat", width=width, show=(show or ""))
            e.pack(side="left", padx=(0, 8), ipady=4)
            return rr, e

        _ent("Share path", self._smb_share_var)
        self._smb_share_var.set(r"\\192.168.0.10\music")
        _ent("Username", self._smb_user_var)
        rr_pw, pw_ent = _ent("Password", self._smb_pass_var, show="●")
        self._smb_show_pw = tk.BooleanVar(value=False)
        tk.Checkbutton(rr_pw, text="Show", variable=self._smb_show_pw,
                       font=FONT_SMALL, fg=TEXT_PRIMARY, bg=BG_CARD,
                       activebackground=BG_CARD, selectcolor=BG_PANEL,
                       cursor="hand2",
                       command=lambda: pw_ent.config(
                           show="" if self._smb_show_pw.get() else "●")
                       ).pack(side="left")

        opt = self._row(p, "")
        tk.Checkbutton(opt, text="Reconnect at sign-in", variable=self._smb_persist_var,
                       font=FONT_SMALL, fg=TEXT_PRIMARY, bg=BG_CARD,
                       activebackground=BG_CARD, selectcolor=BG_PANEL,
                       cursor="hand2").pack(side="left")
        tk.Button(opt, text="Connect & Add", font=FONT_SMALL, fg=BG_DARK, bg=ACCENT,
                  relief="flat", bd=0, cursor="hand2", padx=12, pady=6,
                  command=self._mount_smb_share).pack(side="right")

        tk.Label(p, textvariable=self._smb_status_var, font=FONT_TINY,
                 fg=TEXT_PRIMARY, bg=BG_CARD, wraplength=580,
                 anchor="w", justify="left").pack(fill="x", pady=(4, 2))
        tk.Label(p, text="Maps the share to a drive letter using Windows "
                         "(net use). Enter the path as \\\\server\\share; for a "
                         "domain account use DOMAIN\\user. Leave the username "
                         "blank to use your Windows sign-in. PulseBox does "
                         "not store the password.",
                 font=FONT_TINY, fg=TEXT_SECONDARY, bg=BG_CARD,
                 wraplength=580, anchor="w", justify="left").pack(
            fill="x", pady=(0, 2))

    def _mount_smb_share(self):
        share = self._smb_share_var.get()
        user  = self._smb_user_var.get().strip()
        pw    = self._smb_pass_var.get()
        persist = bool(self._smb_persist_var.get())
        self._smb_status_var.set("Connecting…")

        def _do():
            ok, msg, _path = winplat.map_network_share(share, user, pw, persist)
            def _done():
                self._smb_status_var.set(("✓ " if ok else "✗ ") + msg)
                if ok:
                    self._async_load_drives()        # refresh the list
                    self._smb_pass_var.set("")        # don't leave the pw on screen
            self.after(0, _done)
        threading.Thread(target=_do, daemon=True).start()

    def _unmount_selected_drive(self):
        label = self._net_drive_var.get()
        path = next((m for m, l in self._net_drives if l == label), "")
        if not path:
            self._smb_status_var.set("✗ Select a mapped drive first")
            return
        self._smb_status_var.set(f"Disconnecting {path}…")

        def _do():
            ok, msg = winplat.unmap_network_drive(path)
            def _done():
                self._smb_status_var.set(("✓ " if ok else "✗ ") + msg)
                if ok:
                    self._async_load_drives()
            self.after(0, _done)
        threading.Thread(target=_do, daemon=True).start()

    def _async_load_drives(self):
        def _scan():
            drives = winplat.network_drives()
            self.after(0, lambda: self._update_net_combo(drives))
        threading.Thread(target=_scan, daemon=True).start()

    def _update_net_combo(self, drives):
        self._net_drives = drives
        if self._net_combo:
            labels = [l for _, l in drives] if drives else ["No network drives found"]
            self._net_combo["values"] = labels
            self._net_drive_var.set(labels[0])

    def _apply_net_drive_to_music_dir(self):
        selected_label = self._net_drive_var.get()
        path = next((m for m, l in self._net_drives if l == selected_label), "")
        if path:
            self._vars["music_dir"].set(path)
            messagebox.showinfo("Music Directory Updated",
                                f"Music directory set to:\n{path}\n\n"
                                "Click Rescan Library to index it.",
                                parent=self)
        else:
            messagebox.showwarning("No Drive Selected",
                                   "Please select a network drive first.",
                                   parent=self)

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    def _build_playback(self, p):
        self._add_slider(p, "volume",           "Default Volume",     0, 100)
        self._add_slider(p, "crossfade_seconds", "Crossfade (seconds)", 0, 10)
        self._add_toggle(p, "shuffle",           "Shuffle by default")

        # VLC audio output module
        r_aout = self._row(p, "VLC Audio Output")
        # "auto", not "": Tk draws every radio button in a group as
        # half-selected when the variable is an empty string.
        v_aout = tk.StringVar(value=self._config.get("vlc_aout") or "auto")
        self._vars["vlc_aout"] = v_aout
        for opt, label in [("auto", "Automatic"), ("mmdevice", "WASAPI"),
                           ("directsound", "DirectSound"), ("waveout", "WaveOut")]:
            tk.Radiobutton(r_aout, text=label, variable=v_aout, value=opt,
                           font=FONT_SMALL, fg=TEXT_PRIMARY,
                           bg=BG_CARD, activebackground=BG_CARD,
                           activeforeground=TEXT_PRIMARY,
                           selectcolor=BG_PANEL, cursor="hand2").pack(
                side="left", padx=6)
        tk.Label(p, text="⚠  Restart PulseBox after changing the output type. "
                         "Leave on Automatic unless audio misbehaves.",
                 font=FONT_TINY, fg=WARNING, bg=BG_CARD).pack(
            anchor="w", pady=(0, 4))

        # Playback device
        r_dev = self._row(p, "Playback Device")
        self._audio_combo = ttk.Combobox(r_dev, textvariable=self._audio_dev_var,
                                         state="readonly", font=FONT_BODY, width=44)
        self._audio_combo.pack(side="left", padx=(0, 8), ipady=3)
        tk.Button(r_dev, text="⟳ Refresh", font=FONT_SMALL,
                  fg=ACCENT, bg=BG_PANEL, relief="flat", bd=0,
                  cursor="hand2", padx=8, pady=4,
                  command=self._load_audio_devices).pack(side="left")
        tk.Label(p, text="Applies when you press Save Settings — no restart needed.",
                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_CARD).pack(
            anchor="w", pady=(0, 4))
        self._load_audio_devices()

    def _load_audio_devices(self):
        """Fill the device dropdown from VLC and select the saved device."""
        self._audio_devices = [("", _AUDIO_DEFAULT_LABEL)]
        seen = {_AUDIO_DEFAULT_LABEL}
        for dev_id, desc in self._player.list_output_devices():
            label = desc if desc not in seen else f"{desc}  [{dev_id[-8:]}]"
            seen.add(label)
            self._audio_devices.append((dev_id, label))
        if self._audio_combo:
            self._audio_combo["values"] = [l for _, l in self._audio_devices]
        saved = self._config.get("vlc_audio_device", "")
        self._audio_dev_var.set(next(
            (l for d, l in self._audio_devices if d == saved),
            _AUDIO_DEFAULT_LABEL))

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def _build_display(self, p):
        self._add_choice(p, "repeat", "Repeat Mode",
                         ["none", "track", "queue"])

        r_cols = self._row(p, "Album Grid Columns")
        v_cols = tk.IntVar(value=self._config.get("columns_per_row", 6))
        self._vars["columns_per_row"] = v_cols
        for n in [2, 3, 4, 5, 6, 7, 8, 9, 10]:
            tk.Radiobutton(r_cols, text=str(n), variable=v_cols, value=n,
                           font=FONT_SMALL, fg=TEXT_PRIMARY,
                           bg=BG_CARD, activebackground=BG_CARD,
                           selectcolor=BG_PANEL, cursor="hand2").pack(
                side="left", padx=4)

        self._add_toggle(p, "kiosk_mode", "Kiosk Mode (fullscreen)")

    # ------------------------------------------------------------------
    # About
    # ------------------------------------------------------------------

    def _build_about(self, p):
        tk.Label(p, text="PulseBox",
                 font=FONT_HEADING, fg=ACCENT,
                 bg=BG_CARD, anchor="w").pack(fill="x")
        tk.Label(p, text=f"Version {APP_VERSION}  ·  Python / Tkinter / VLC / SQLite",
                 font=FONT_SMALL, fg=TEXT_PRIMARY,
                 bg=BG_CARD, anchor="w").pack(fill="x")
        tk.Label(p, text="Created by Andrew J. Morgan",
                 font=FONT_SMALL, fg=TEXT_PRIMARY,
                 bg=BG_CARD, anchor="w").pack(fill="x", pady=(4, 0))
        tk.Label(p, text="Full credits and licence available in the About page (ℹ button in menu bar)",
                 font=FONT_TINY, fg=TEXT_PRIMARY,
                 bg=BG_CARD, anchor="w").pack(fill="x", pady=(2, 0))

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _save(self, quiet=False):
        for key, var in self._vars.items():
            self._config.set(key, var.get())
        # Playback device: store VLC's device id (not the label) and switch live
        label = self._audio_dev_var.get()
        known = [d for d, l in self._audio_devices if l == label]
        if known:
            dev_id = known[0]
            if dev_id != self._config.get("vlc_audio_device", ""):
                self._config.set("vlc_audio_device", dev_id)
                self._player.set_output_device(dev_id)
        if not quiet:
            messagebox.showinfo("Settings", "Settings saved.", parent=self)
