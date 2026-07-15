"""
PulseBox Settings Page
=======================
Settings grouped into collapsible cards — same pattern as the Modules page.
Click a section header or the + button to expand.  All settings and save
behaviour are identical to the previous flat layout.
"""

import os
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .theme import (ACCENT, ACCENT2, BG_CARD, BG_DARK, BG_PANEL,
                    BORDER, DANGER, FONT_BODY, FONT_DISPLAY, FONT_HEADING,
                    FONT_SMALL, FONT_SUBHEAD, FONT_TINY, NEON_BLUE, PAD,
                    PAD_SM, SUCCESS, TEXT_DIM, TEXT_PRIMARY, TEXT_SECONDARY,
                    WARNING)
from .widgets import ScrollableFrame
from core.config import APP_VERSION


# ---------------------------------------------------------------------------
# Colour cycle — one per section card (mirrors modules page)
# ---------------------------------------------------------------------------
_SECTION_COLOURS = [
    "#e8b004",  # amber      — Music Library
    "#00d4ff",  # cyan       — Video Library
    "#ff2d78",  # pink       — WiFi
    "#4488ff",  # blue       — Network Drives
    "#00cc88",  # green      — Playback
    "#cc44ff",  # purple     — Display
    "#ff6b35",  # orange     — Screen Rotation
    "#aaaaaa",  # grey       — About
]


# ---------------------------------------------------------------------------
# System helpers (unchanged from original)
# ---------------------------------------------------------------------------

def _scan_wifi_networks() -> list[tuple[str, str]]:
    try:
        out = subprocess.check_output(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list"],
            stderr=subprocess.DEVNULL, timeout=8, text=True)
        seen, networks = set(), []
        for line in out.strip().splitlines():
            parts = line.split(":")
            ssid = parts[0].strip()
            if ssid and ssid not in seen:
                seen.add(ssid)
                signal   = parts[1].strip() if len(parts) > 1 else "?"
                security = parts[2].strip() if len(parts) > 2 else ""
                networks.append((ssid,
                    f"{ssid}  ({signal}%{' 🔒' if security else ''})"))
        return networks
    except Exception:
        return []


def _connect_wifi(ssid: str, password: str) -> tuple[bool, str]:
    try:
        r = subprocess.run(["nmcli", "con", "up", ssid],
                           capture_output=True, text=True, timeout=20)
        if r.returncode == 0:
            return True, f"Connected to {ssid}"
        cmd = ["nmcli", "dev", "wifi", "connect", ssid]
        if password:
            cmd += ["password", password]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            return True, f"Connected to {ssid}"
        return False, r.stderr.strip() or "Connection failed"
    except subprocess.TimeoutExpired:
        return False, "Connection timed out"
    except FileNotFoundError:
        return False, "nmcli not found"
    except Exception as e:
        return False, str(e)


def _get_current_ssid() -> str:
    try:
        out = subprocess.check_output(
            ["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"],
            stderr=subprocess.DEVNULL, timeout=5, text=True)
        for line in out.splitlines():
            parts = line.split(":")
            if parts[0].strip().lower() == "yes" and len(parts) > 1:
                return parts[1].strip()
    except Exception:
        pass
    return ""


def _get_network_drives() -> list[tuple[str, str]]:
    drives, seen = [], set()
    network_types = {"cifs","nfs","nfs4","smbfs","fuse.sshfs",
                     "fuse.rclone","davfs","ftp","ftps"}
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3:
                    device, mount, fstype = parts[0], parts[1], parts[2]
                    if fstype in network_types and mount not in seen:
                        seen.add(mount)
                        drives.append((mount,
                            f"{os.path.basename(mount)}  [{fstype}]  {device}"))
    except Exception:
        pass
    for base in [f"/media/{os.environ.get('USER','pi')}", "/media", "/mnt"]:
        try:
            for name in os.listdir(base):
                path = os.path.join(base, name)
                if os.path.isdir(path) and os.path.ismount(path) and path not in seen:
                    seen.add(path)
                    drives.append((path, f"{name}  (mounted at {path})"))
        except Exception:
            pass
    return drives


def _get_current_rotation() -> int:
    try:
        with open("/boot/firmware/config.txt") as f:
            for line in f:
                if line.strip().startswith("display_rotate="):
                    val = int(line.split("=")[1])
                    return {0:0,1:90,2:180,3:270}.get(val, 0)
    except Exception:
        pass
    return 0


def _set_rotation(degrees: int) -> tuple[bool, str]:
    import tempfile
    rot_map = {0:0, 90:1, 180:2, 270:3}
    val = rot_map.get(degrees, 0)
    try:
        with open("/boot/firmware/config.txt") as f:
            lines = f.readlines()
    except Exception as e:
        return False, f"Cannot read config.txt: {e}"
    lines = [l for l in lines if not l.strip().startswith("display_rotate=")]
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines.append(f"display_rotate={val}  # PulseBox\n")
    new_content = "".join(lines)
    try:
        with open("/boot/firmware/config.txt", "w") as f:
            f.write(new_content)
        return True, f"Rotation set to {degrees}°. Reboot to apply."
    except PermissionError:
        pass
    try:
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tmp:
            tmp.write(new_content)
            tmp_path = tmp.name
        r = subprocess.run(["sudo","cp",tmp_path,"/boot/firmware/config.txt"],
                           capture_output=True, text=True, timeout=8)
        os.unlink(tmp_path)
        if r.returncode == 0:
            return True, f"Rotation set to {degrees}°. Reboot to apply."
        return False, "sudo cp failed — run PulseBox with sudo or add sudoers entry."
    except Exception as e:
        return False, str(e)


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
                              font=("Helvetica", 16, "bold"),
                              fg=self._accent, bg=BG_PANEL,
                              padx=8, pady=10)
        self._icon.pack(side="left")

        tk.Label(hdr, text=title,
                 font=FONT_SUBHEAD, fg=TEXT_PRIMARY,
                 bg=BG_PANEL, pady=10).pack(side="left")

        self._btn = tk.Button(hdr, text=" + ",
                              font=("Helvetica", 14, "bold"),
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

        # WiFi state
        self._wifi_networks:  list[tuple[str,str]] = []
        self._wifi_ssid_var   = tk.StringVar()
        self._wifi_pass_var   = tk.StringVar()
        self._wifi_status_var = tk.StringVar(value="")
        self._wifi_combo: ttk.Combobox | None = None
        self._wifi_scanning   = False

        # Network drives
        self._net_drives: list[tuple[str,str]] = []
        self._net_drive_var   = tk.StringVar()
        self._net_combo: ttk.Combobox | None = None

        self._build()
        self._async_load_wifi()
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
        _card("WiFi",             2, self._build_wifi)
        _card("Network Drives",   3, self._build_netdrive)
        _card("Playback",         4, self._build_playback)
        _card("Display",          5, self._build_display)
        _card("Screen Rotation",  6, self._build_rotation)
        _card("About",            7, self._build_about)

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
                       selectcolor=BG_PANEL, cursor="hand2").pack(side="left")

    def _add_choice(self, parent, key, label, options):
        r = self._row(parent, label)
        v = tk.StringVar(value=self._config.get(key))
        self._vars[key] = v
        for opt in options:
            tk.Radiobutton(r, text=opt.capitalize(), variable=v, value=opt,
                           font=FONT_SMALL, fg=TEXT_PRIMARY,
                           bg=BG_CARD, activebackground=BG_CARD,
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
    # WiFi
    # ------------------------------------------------------------------

    def _build_wifi(self, p):
        self._wifi_current_lbl = tk.Label(
            p, text=f"Current: {_get_current_ssid() or 'Not connected'}",
            font=FONT_SMALL, fg=TEXT_PRIMARY, bg=BG_CARD)
        self._wifi_current_lbl.pack(anchor="w", pady=(0, 4))

        r1 = self._row(p, "Network")
        self._wifi_combo = ttk.Combobox(r1, textvariable=self._wifi_ssid_var,
                                        state="readonly", font=FONT_BODY, width=30)
        self._wifi_combo.pack(side="left", padx=(0, 8), ipady=3)
        self._wifi_combo["values"] = ["Scanning…"]
        self._wifi_scan_btn = tk.Button(
            r1, text="⟳ Scan", font=FONT_SMALL,
            fg=ACCENT, bg=BG_PANEL, relief="flat", bd=0,
            cursor="hand2", padx=8, pady=4,
            command=self._async_load_wifi)
        self._wifi_scan_btn.pack(side="left")

        r2 = self._row(p, "Password")
        pw_ent = tk.Entry(r2, textvariable=self._wifi_pass_var,
                          show="●", font=FONT_BODY, bg=BG_PANEL,
                          fg=TEXT_PRIMARY, insertbackground=ACCENT,
                          relief="flat", width=30)
        pw_ent.pack(side="left", padx=(0, 8), ipady=4)
        self._show_pw = tk.BooleanVar(value=False)
        tk.Checkbutton(r2, text="Show", variable=self._show_pw,
                       font=FONT_SMALL, fg=TEXT_PRIMARY, bg=BG_CARD,
                       activebackground=BG_CARD, selectcolor=BG_PANEL,
                       cursor="hand2",
                       command=lambda: pw_ent.config(
                           show="" if self._show_pw.get() else "●")
                       ).pack(side="left")

        btn_row = tk.Frame(p, bg=BG_CARD)
        btn_row.pack(fill="x", pady=(4, 2))
        tk.Label(btn_row, textvariable=self._wifi_status_var,
                 font=FONT_SMALL, fg=SUCCESS, bg=BG_CARD).pack(side="left")
        tk.Button(btn_row, text="Connect",
                  font=FONT_SMALL, fg=BG_DARK, bg=ACCENT,
                  relief="flat", bd=0, cursor="hand2", padx=12, pady=6,
                  command=self._connect_wifi).pack(side="right")

    def _async_load_wifi(self):
        if self._wifi_scanning:
            return
        self._wifi_scanning = True
        if self._wifi_combo:
            self._wifi_combo["values"] = ["Scanning…"]
        if hasattr(self, "_wifi_scan_btn"):
            self._wifi_scan_btn.config(state="disabled", text="Scanning…")

        def _scan():
            nets = _scan_wifi_networks()
            self.after(0, lambda: self._update_wifi_combo(nets))

        threading.Thread(target=_scan, daemon=True).start()

    def _update_wifi_combo(self, nets):
        self._wifi_networks = nets
        self._wifi_scanning = False
        if hasattr(self, "_wifi_scan_btn"):
            self._wifi_scan_btn.config(state="normal", text="⟳ Scan")
        if self._wifi_combo:
            labels = [label for _, label in nets] if nets else ["No networks found"]
            self._wifi_combo["values"] = labels
            current = _get_current_ssid()
            for ssid, label in nets:
                if ssid == current:
                    self._wifi_ssid_var.set(label)
                    break
            if hasattr(self, "_wifi_current_lbl"):
                self._wifi_current_lbl.config(
                    text=f"Current: {current or 'Not connected'}")

    def _connect_wifi(self):
        selected_label = self._wifi_ssid_var.get()
        ssid = next((s for s, l in self._wifi_networks if l == selected_label), "")
        if not ssid:
            self._wifi_status_var.set("⚠ Select a network first")
            return
        self._wifi_status_var.set(f"Connecting to {ssid}…")

        def _do():
            ok, msg = _connect_wifi(ssid, self._wifi_pass_var.get())
            self.after(0, lambda: self._wifi_status_var.set(
                ("✓ " if ok else "✗ ") + msg))
            self.after(0, lambda: self._wifi_current_lbl.config(
                text=f"Current: {_get_current_ssid() or 'Not connected'}"))

        threading.Thread(target=_do, daemon=True).start()

    # ------------------------------------------------------------------
    # Network Drives
    # ------------------------------------------------------------------

    def _build_netdrive(self, p):
        r = self._row(p, "Mounted Drive")
        self._net_combo = ttk.Combobox(r, textvariable=self._net_drive_var,
                                       state="readonly", font=FONT_BODY, width=36)
        self._net_combo.pack(side="left", padx=(0, 8), ipady=3)
        self._net_combo["values"] = ["Scanning…"]
        tk.Button(r, text="⟳ Refresh", font=FONT_SMALL,
                  fg=ACCENT, bg=BG_PANEL, relief="flat", bd=0,
                  cursor="hand2", padx=8, pady=4,
                  command=self._async_load_drives).pack(side="left")

        tk.Label(p, text='Select a drive then click "Use Selected Drive →" in Music Library.',
                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_CARD,
                 wraplength=580, anchor="w", justify="left").pack(
            fill="x", pady=(4, 2))

    def _async_load_drives(self):
        def _scan():
            drives = _get_network_drives()
            self.after(0, lambda: self._update_net_combo(drives))
        threading.Thread(target=_scan, daemon=True).start()

    def _update_net_combo(self, drives):
        self._net_drives = drives
        if self._net_combo:
            labels = [l for _, l in drives] if drives else ["No network drives found"]
            self._net_combo["values"] = labels
            if drives:
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

        # VLC audio output
        r_aout = self._row(p, "VLC Audio Output")
        v_aout = tk.StringVar(value=self._config.get("vlc_aout", "pipewire"))
        self._vars["vlc_aout"] = v_aout
        for opt, label in [("pipewire","PipeWire"),("alsa","ALSA"),
                            ("pulse","PulseAudio"),("","VLC default")]:
            tk.Radiobutton(r_aout, text=label, variable=v_aout, value=opt,
                           font=FONT_SMALL, fg=TEXT_PRIMARY,
                           bg=BG_CARD, activebackground=BG_CARD,
                           selectcolor=BG_PANEL, cursor="hand2").pack(
                side="left", padx=6)
        tk.Label(p, text="⚠  Restart PulseBox after changing audio output.",
                 font=FONT_TINY, fg=WARNING, bg=BG_CARD).pack(
            anchor="w", pady=(0, 4))

        # ALSA device
        r_alsa = self._row(p, "ALSA Device")
        v_alsa = tk.StringVar(value=self._config.get("vlc_alsa_device", ""))
        self._vars["vlc_alsa_device"] = v_alsa
        tk.Entry(r_alsa, textvariable=v_alsa, font=FONT_BODY, bg=BG_PANEL,
                 fg=TEXT_PRIMARY, insertbackground=ACCENT,
                 relief="flat", width=14).pack(side="left", ipady=3, padx=(0, 8))
        tk.Label(r_alsa, text="e.g. hw:1,0  (blank = ALSA default)",
                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_CARD).pack(side="left")
        tk.Button(r_alsa, text="Detect Cards",
                  font=FONT_TINY, fg=ACCENT, bg=BG_PANEL,
                  relief="flat", bd=0, cursor="hand2", padx=6, pady=3,
                  command=lambda: self._detect_alsa_cards(v_alsa)).pack(side="right")

        self._alsa_cards_lbl = tk.Label(p, text="", font=FONT_TINY,
                                        fg=TEXT_PRIMARY, bg=BG_CARD,
                                        justify="left", anchor="w")
        self._alsa_cards_lbl.pack(fill="x", pady=(0, 4))

    def _detect_alsa_cards(self, var):
        def _run():
            try:
                r = subprocess.run(["aplay", "-l"],
                                   capture_output=True, text=True, timeout=5)
                lines, device_to_set = [], ""
                for line in r.stdout.splitlines():
                    if line.startswith("card "):
                        parts = line.split(":")
                        card_num = parts[0].replace("card","").strip()
                        name     = parts[1].strip() if len(parts) > 1 else ""
                        lines.append(f"  hw:{card_num},0  {name}")
                        if "hifiberry" in line.lower() or "wm8804" in line.lower():
                            device_to_set = f"hw:{card_num},0"
                text = ("Available cards:\n" + "\n".join(lines)) if lines else "No cards found"
                def _update():
                    self._alsa_cards_lbl.config(text=text)
                    if device_to_set and not var.get():
                        var.set(device_to_set)
                self.after(0, _update)
            except Exception as e:
                self.after(0, lambda: self._alsa_cards_lbl.config(
                    text=f"aplay error: {e}"))
        threading.Thread(target=_run, daemon=True).start()
        self._alsa_cards_lbl.config(text="Scanning…")

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
    # Screen Rotation
    # ------------------------------------------------------------------

    def _build_rotation(self, p):
        r = self._row(p, "Display Rotation")
        current = _get_current_rotation()
        self._rotation_var = tk.IntVar(value=current)
        for deg in [0, 90, 180, 270]:
            tk.Radiobutton(r, text=f"{deg}°",
                           variable=self._rotation_var, value=deg,
                           font=FONT_SMALL, fg=TEXT_PRIMARY,
                           bg=BG_CARD, activebackground=BG_CARD,
                           selectcolor=BG_PANEL, cursor="hand2").pack(
                side="left", padx=8)

        self._rotation_status = tk.StringVar(value=f"Current: {current}°")
        status_row = tk.Frame(p, bg=BG_CARD)
        status_row.pack(fill="x", pady=(4, 2))
        tk.Label(status_row, textvariable=self._rotation_status,
                 font=FONT_SMALL, fg=TEXT_PRIMARY, bg=BG_CARD).pack(side="left")
        tk.Button(status_row, text="Apply Rotation",
                  font=FONT_SMALL, fg=BG_PANEL, bg=NEON_BLUE,
                  relief="flat", bd=0, cursor="hand2", padx=12, pady=5,
                  command=self._apply_rotation).pack(side="right")

        tk.Label(p, text="⚠  Reboot required after changing rotation.",
                 font=FONT_TINY, fg=WARNING, bg=BG_CARD).pack(
            anchor="w", pady=(0, 4))

    def _apply_rotation(self):
        deg = self._rotation_var.get()
        ok, msg = _set_rotation(deg)
        self._rotation_status.set(msg)
        if ok:
            if messagebox.askyesno("Reboot Required",
                                   f"Rotation set to {deg}°.\nReboot now to apply?",
                                   parent=self):
                def _do():
                    import time; time.sleep(0.5)
                    try:
                        r = subprocess.run(["systemctl","reboot"],
                                           capture_output=True, timeout=5)
                        if r.returncode != 0:
                            subprocess.run(["sudo","reboot"], timeout=5)
                    except Exception as e:
                        self.after(0, lambda: messagebox.showerror(
                            "Reboot failed",
                            f"{e}\n\nRun manually: sudo reboot",
                            parent=self))
                threading.Thread(target=_do, daemon=True).start()
        else:
            messagebox.showerror("Rotation Error", msg, parent=self)

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
        if not quiet:
            messagebox.showinfo("Settings", "Settings saved.", parent=self)
