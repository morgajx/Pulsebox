"""
PulseBox Module: Bluetooth Manager
===================================
Scan for Bluetooth devices, pair, connect, disconnect and trust them.
Uses bluetoothctl via subprocess — works on Raspberry Pi OS Trixie/Bookworm.

Requirements (usually pre-installed on Pi OS):
    sudo apt install bluetooth bluez bluez-tools
    sudo systemctl enable bluetooth
    sudo systemctl start bluetooth
"""

import re
import subprocess
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from core.config import JukeboxModule, APP_VERSION


# ---------------------------------------------------------------------------
# bluetoothctl helpers
# ---------------------------------------------------------------------------

def _bt_cmd(args: list[str], timeout: int = 8) -> tuple[bool, str]:
    """Run a bluetoothctl command, return (success, output)."""
    try:
        r = subprocess.run(
            ["bluetoothctl"] + args,
            capture_output=True, text=True, timeout=timeout)
        out = r.stdout.strip() + r.stderr.strip()
        return r.returncode == 0, out
    except FileNotFoundError:
        return False, "bluetoothctl not found. Install: sudo apt install bluez"
    except subprocess.TimeoutExpired:
        return False, "Command timed out"
    except Exception as e:
        return False, str(e)


def _bt_is_powered() -> bool:
    ok, out = _bt_cmd(["show"])
    return "Powered: yes" in out


def _bt_set_power(on: bool) -> tuple[bool, str]:
    return _bt_cmd(["power", "on" if on else "off"])


def _bt_scan_start():
    subprocess.Popen(["bluetoothctl", "scan", "on"],
                     stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


def _bt_scan_stop():
    subprocess.Popen(["bluetoothctl", "scan", "off"],
                     stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


def _bt_get_devices() -> list[dict]:
    """
    Return list of known devices: {mac, name, connected, paired, trusted}.
    """
    _, out = _bt_cmd(["devices"])
    devices = []
    for line in out.splitlines():
        m = re.match(r"Device ([0-9A-F:]{17})\s+(.*)", line, re.I)
        if m:
            mac  = m.group(1)
            name = m.group(2).strip()
            _, info = _bt_cmd(["info", mac])
            connected = "Connected: yes"  in info
            paired    = "Paired: yes"     in info
            trusted   = "Trusted: yes"    in info
            devices.append({
                "mac": mac, "name": name,
                "connected": connected,
                "paired":    paired,
                "trusted":   trusted,
            })
    return devices


def _bt_pair(mac: str) -> tuple[bool, str]:
    return _bt_cmd(["pair", mac], timeout=30)


def _bt_connect(mac: str) -> tuple[bool, str]:
    return _bt_cmd(["connect", mac], timeout=20)


def _bt_disconnect(mac: str) -> tuple[bool, str]:
    return _bt_cmd(["disconnect", mac], timeout=10)


def _bt_trust(mac: str) -> tuple[bool, str]:
    return _bt_cmd(["trust", mac])


def _bt_remove(mac: str) -> tuple[bool, str]:
    return _bt_cmd(["remove", mac])


def _bt_get_connected_audio() -> list[dict]:
    """Return only connected devices that look like audio sinks."""
    audio_hints = ["headphone", "headset", "speaker", "earbuds",
                   "audio", "bt", "soundbar", "airpod", "buds"]
    devices = _bt_get_devices()
    connected = [d for d in devices if d["connected"]]
    # Tag likely audio devices
    for d in connected:
        d["is_audio"] = any(h in d["name"].lower() for h in audio_hints)
    return connected


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------

class BluetoothManager(JukeboxModule):
    MODULE_ID          = "bluetooth_manager"
    MODULE_NAME        = "Bluetooth Manager"
    MODULE_VERSION = APP_VERSION
    MODULE_DESCRIPTION = ("Scan, pair, connect and manage Bluetooth audio devices. "
                          "Requires bluez (sudo apt install bluez).")
    MODULE_AUTHOR      = "Andrew J. Morgan"

    SCAN_DURATION = 12   # seconds to scan before auto-stop

    def __init__(self):
        super().__init__()
        self._scanning        = False
        self._powered         = False
        self._devices: list   = []
        self._status_var: tk.StringVar | None = None
        self._power_var:  tk.BooleanVar | None = None
        self._device_frame: tk.Frame | None = None
        self._scan_btn: tk.Button | None = None

    def on_load(self, app):
        super().on_load(app)
        self._powered = _bt_is_powered()

    # ------------------------------------------------------------------
    # UI Panel
    # ------------------------------------------------------------------

    def get_ui_panel(self, parent) -> tk.Frame:
        from ui.theme import (BG_PANEL, BG_CARD, BG_DARK, ACCENT, NEON_BLUE,
                              SUCCESS, DANGER, WARNING, TEXT_PRIMARY,
                              TEXT_SECONDARY, TEXT_DIM,
                              FONT_BODY, FONT_SMALL, FONT_TINY,
                              FONT_SUBHEAD, PAD, PAD_SM)

        frame = tk.Frame(parent, bg=BG_PANEL)

        # ── Bluetooth on/off ──────────────────────────────────────────
        top = tk.Frame(frame, bg=BG_PANEL)
        top.pack(fill="x", pady=(0, 8))

        self._power_var = tk.BooleanVar(value=self._powered)
        tk.Checkbutton(
            top, text="Bluetooth Enabled",
            variable=self._power_var,
            font=FONT_BODY, fg=TEXT_PRIMARY,
            bg=BG_PANEL, activebackground=BG_PANEL,
            selectcolor=BG_CARD, cursor="hand2",
            command=self._toggle_power
        ).pack(side="left")

        self._status_var = tk.StringVar(
            value="On" if self._powered else "Off")
        tk.Label(top, textvariable=self._status_var,
                 font=FONT_SMALL, fg=NEON_BLUE,
                 bg=BG_PANEL).pack(side="left", padx=8)

        # ── Scan button ───────────────────────────────────────────────
        self._scan_btn = tk.Button(
            top, text="🔍 Scan for Devices",
            font=FONT_SMALL, fg=BG_PANEL, bg=ACCENT,
            relief="flat", bd=0, cursor="hand2",
            padx=12, pady=5,
            command=self._start_scan)
        self._scan_btn.pack(side="right")

        # ── Device list ───────────────────────────────────────────────
        tk.Label(frame, text="Known Devices",
                 font=FONT_SMALL, fg=TEXT_PRIMARY,
                 bg=BG_PANEL).pack(anchor="w", pady=(0, 4))

        self._device_frame = tk.Frame(frame, bg=BG_PANEL)
        self._device_frame.pack(fill="x")

        # ── Setup hint ────────────────────────────────────────────────
        tk.Label(frame,
                 text="After connecting, set Bluetooth as audio output in:\n"
                      "  Audio Output Configurator → or via system sound settings.",
                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_PANEL,
                 justify="left", anchor="w").pack(
            fill="x", pady=(8, 0))

        self._refresh_devices()
        return frame

    # ------------------------------------------------------------------
    # Power toggle
    # ------------------------------------------------------------------

    def _toggle_power(self):
        on = self._power_var.get()
        self._status_var.set("Turning on…" if on else "Turning off…")

        def _run():
            ok, msg = _bt_set_power(on)
            self._powered = ok and on
            status = ("On" if self._powered else "Off") + (
                "" if ok else f"  (error: {msg})")
            if self._status_var and self.app:
                self.app.root.after(0, lambda: self._status_var.set(status))
            if self._powered:
                self.app.root.after(0, self._refresh_devices)

        threading.Thread(target=_run, daemon=True).start()

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def _start_scan(self):
        if self._scanning:
            return
        if not self._powered:
            messagebox.showwarning("Bluetooth",
                                   "Enable Bluetooth first.", parent=self._scan_btn)
            return
        self._scanning = True
        self._scan_btn.config(state="disabled", text="Scanning…")
        self._status_var.set(f"Scanning for {self.SCAN_DURATION}s…")

        def _run():
            _bt_scan_start()
            # Poll every 3s during scan window
            for _ in range(self.SCAN_DURATION // 3):
                time.sleep(3)
                if self.app:
                    self.app.root.after(0, self._refresh_devices)
            _bt_scan_stop()
            self._scanning = False
            if self.app:
                self.app.root.after(0, self._scan_done)

        threading.Thread(target=_run, daemon=True).start()

    def _scan_done(self):
        if self._scan_btn:
            self._scan_btn.config(state="normal", text="🔍 Scan for Devices")
        self._status_var.set("Scan complete")
        self._refresh_devices()

    # ------------------------------------------------------------------
    # Device list
    # ------------------------------------------------------------------

    def _refresh_devices(self):
        if not self._device_frame:
            return

        def _fetch():
            devices = _bt_get_devices()
            if self.app:
                self.app.root.after(0, lambda: self._render_devices(devices))

        threading.Thread(target=_fetch, daemon=True).start()

    def _render_devices(self, devices: list):
        from ui.theme import (BG_PANEL, BG_CARD, BG_CARD_HOVER,
                              ACCENT, NEON_BLUE, SUCCESS, DANGER,
                              WARNING, TEXT_PRIMARY, TEXT_SECONDARY,
                              TEXT_DIM, FONT_BODY, FONT_SMALL, FONT_TINY)

        for w in self._device_frame.winfo_children():
            w.destroy()

        self._devices = devices

        if not devices:
            tk.Label(self._device_frame,
                     text="No devices found. Click 'Scan for Devices'.",
                     font=FONT_SMALL, fg=TEXT_PRIMARY,
                     bg=BG_PANEL).pack(anchor="w", padx=4, pady=4)
            return

        for dev in devices:
            self._device_row(dev)

    def _device_row(self, dev: dict):
        from ui.theme import (BG_PANEL, BG_CARD, ACCENT, NEON_BLUE,
                              SUCCESS, DANGER, TEXT_PRIMARY,
                              TEXT_SECONDARY, TEXT_DIM,
                              FONT_BODY, FONT_SMALL, FONT_TINY)

        bg  = BG_CARD if dev["connected"] else BG_PANEL
        row = tk.Frame(self._device_frame, bg=bg)
        row.pack(fill="x", pady=2, padx=2)

        # Status dot
        dot_col = SUCCESS if dev["connected"] else (
            NEON_BLUE if dev["paired"] else TEXT_DIM)
        tk.Label(row, text="●", font=FONT_SMALL,
                 fg=dot_col, bg=bg).pack(side="left", padx=(6, 4))

        # Name + MAC
        info = tk.Frame(row, bg=bg)
        info.pack(side="left", fill="x", expand=True)
        tk.Label(info, text=dev["name"] or dev["mac"],
                 font=FONT_BODY, fg=TEXT_PRIMARY,
                 bg=bg, anchor="w").pack(fill="x")
        tags = []
        if dev["connected"]: tags.append("Connected")
        if dev["paired"]:    tags.append("Paired")
        if dev["trusted"]:   tags.append("Trusted")
        tk.Label(info, text=dev["mac"] + (
                     "  ·  " + " · ".join(tags) if tags else ""),
                 font=FONT_TINY, fg=TEXT_PRIMARY,
                 bg=bg, anchor="w").pack(fill="x")

        # Action buttons
        btns = tk.Frame(row, bg=bg)
        btns.pack(side="right", padx=6)

        if dev["connected"]:
            tk.Button(btns, text="Disconnect",
                      font=FONT_TINY, fg=DANGER, bg=bg,
                      relief="flat", bd=0, cursor="hand2",
                      padx=6, pady=3,
                      command=lambda m=dev["mac"]: self._do_disconnect(m)
                      ).pack(side="left", padx=2)
        else:
            if dev["paired"]:
                tk.Button(btns, text="Connect",
                          font=FONT_TINY, fg=bg, bg=ACCENT,
                          relief="flat", bd=0, cursor="hand2",
                          padx=6, pady=3,
                          command=lambda m=dev["mac"]: self._do_connect(m)
                          ).pack(side="left", padx=2)
            else:
                tk.Button(btns, text="Pair",
                          font=FONT_TINY, fg=bg, bg=NEON_BLUE,
                          relief="flat", bd=0, cursor="hand2",
                          padx=6, pady=3,
                          command=lambda m=dev["mac"]: self._do_pair(m)
                          ).pack(side="left", padx=2)

        if not dev["trusted"] and dev["paired"]:
            tk.Button(btns, text="Trust",
                      font=FONT_TINY, fg=TEXT_PRIMARY, bg=bg,
                      relief="flat", bd=0, cursor="hand2",
                      padx=6, pady=3,
                      command=lambda m=dev["mac"]: self._do_trust(m)
                      ).pack(side="left", padx=2)

        tk.Button(btns, text="✕",
                  font=FONT_TINY, fg=DANGER, bg=bg,
                  relief="flat", bd=0, cursor="hand2",
                  padx=4, pady=3,
                  command=lambda m=dev["mac"]: self._do_remove(m)
                  ).pack(side="left", padx=2)

    # ------------------------------------------------------------------
    # Actions (all run in background threads)
    # ------------------------------------------------------------------

    def _do_pair(self, mac: str):
        self._status_var.set(f"Pairing {mac}…")

        def _run():
            ok, msg = _bt_pair(mac)
            if ok:
                _bt_trust(mac)
            status = f"{'Paired' if ok else 'Pair failed'}: {mac}"
            if self._status_var and self.app:
                self.app.root.after(0, lambda: self._status_var.set(status))
            self.app.root.after(0, self._refresh_devices)
            self.emit_notification(
                f"{'Paired' if ok else 'Pairing failed'}: {mac}", 
                "success" if ok else "error")

        threading.Thread(target=_run, daemon=True).start()

    def _do_connect(self, mac: str):
        self._status_var.set(f"Connecting {mac}…")

        def _run():
            ok, msg = _bt_connect(mac)
            status = f"{'Connected' if ok else 'Failed'}: {mac}"
            if self._status_var and self.app:
                self.app.root.after(0, lambda: self._status_var.set(status))
            self.app.root.after(0, self._refresh_devices)
            self.emit_notification(
                f"{'Connected to' if ok else 'Failed to connect'}: {mac}",
                "success" if ok else "error")

        threading.Thread(target=_run, daemon=True).start()

    def _do_disconnect(self, mac: str):
        def _run():
            ok, _ = _bt_disconnect(mac)
            if self.app:
                self.app.root.after(0, self._refresh_devices)
            self.emit_notification(
                f"Disconnected {mac}" if ok else f"Disconnect failed {mac}",
                "info" if ok else "warning")

        threading.Thread(target=_run, daemon=True).start()

    def _do_trust(self, mac: str):
        def _run():
            ok, _ = _bt_trust(mac)
            if self.app:
                self.app.root.after(0, self._refresh_devices)

        threading.Thread(target=_run, daemon=True).start()

    def _do_remove(self, mac: str):
        if messagebox.askyesno("Remove Device",
                               f"Remove {mac} from known devices?"):
            def _run():
                _bt_remove(mac)
                if self.app:
                    self.app.root.after(0, self._refresh_devices)

            threading.Thread(target=_run, daemon=True).start()
