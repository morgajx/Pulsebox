"""
PulseBox Module: Audio Output Configurator
=========================================
Allows selection of audio output device:

  - I2C mode : discovers audio-related overlays two ways — by globbing the
               overlays directory (/boot/firmware/overlays/*.dtbo) for names
               matching an audio pattern, and by parsing the overlays README
               (which also supplies friendly descriptions). Presents them as
               a dropdown and writes the appropriate dtoverlay= line to
               /boot/firmware/config.txt.

  - HDMI mode : presents HDMI1 / HDMI2 as options and writes
               hdmi_drive / audio output config to config.txt.

Changes take effect after a reboot.
Requires sudo access for writing /boot/firmware/config.txt.
"""

import os
import re
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from core.config import JukeboxModule, APP_VERSION

CONFIG_TXT       = "/boot/firmware/config.txt"
ASOUND_CONF      = "/etc/asound.conf"
WP_CONF_DIR      = os.path.expanduser("~/.config/wireplumber/wireplumber.conf.d")
WP_CONF_FILE     = os.path.join(WP_CONF_DIR, "50-pulsebox-audio.conf")
# Overlay locations. On Bookworm/Trixie these live under /boot/firmware;
# older Raspberry Pi OS images use /boot directly. Resolve at runtime.
OVERLAYS_DIR_CANDIDATES    = ("/boot/firmware/overlays", "/boot/overlays")
OVERLAY_README_CANDIDATES  = ("/boot/firmware/overlays/README",
                              "/boot/overlays/README")

# Match audio-related overlays by filename stem or README description.
AUDIO_OVERLAY_RE = re.compile(
    r"audio|dac|adc|amp|hifiberry|iqaudio|digi|wm8804|sound",
    re.IGNORECASE,
)

def _overlays_dir() -> str:
    """Return the first overlays directory that exists, or the primary path."""
    for d in OVERLAYS_DIR_CANDIDATES:
        if os.path.isdir(d):
            return d
    return OVERLAYS_DIR_CANDIDATES[0]

def _overlay_readme() -> str:
    """Return the first overlays README that exists, or '' if none found."""
    for p in OVERLAY_README_CANDIDATES:
        if os.path.isfile(p):
            return p
    return ""

# Resolved at import time for use elsewhere in the module
OVERLAYS_DIR     = _overlays_dir()
MANAGED_MARKER   = "# PulseBox audio output"   # line marker we own

# Overlays that use the wm8804 chip and need the i2c baudrate fix on Pi 5
I2C_FIX_OVERLAYS = {"hifiberry-digi", "hifiberry-digi-pro"}

# Friendly display names for known overlays
OVERLAY_FRIENDLY = {
    "hifiberry-digi":            "HiFiBerry Digi (S/PDIF optical/coax)",
    "hifiberry-digi-pro":        "HiFiBerry Digi+ Pro (S/PDIF hi-res)",
    "hifiberry-dacplus-std":     "HiFiBerry DAC+ Standard (RCA analogue)",
    "hifiberry-dacplus-pro":     "HiFiBerry DAC+ Pro (RCA hi-res)",
    "hifiberry-dacplushd":       "HiFiBerry DAC2 HD (RCA audiophile)",
    "hifiberry-dacplusadc":      "HiFiBerry DAC+ ADC (RCA in+out)",
    "hifiberry-dacplusadcpro":   "HiFiBerry DAC+ ADC Pro (RCA in+out hi-res)",
    "hifiberry-dacplus":         "HiFiBerry DAC+ (RCA analogue)",
    "hifiberry-dac":             "HiFiBerry DAC (RCA analogue)",
    "hifiberry-amp":             "HiFiBerry Amp (integrated amplifier)",
    "hifiberry-amp2":            "HiFiBerry Amp2 (integrated amplifier)",
    "iqaudio-dacplus":           "IQaudIO DAC+ (RCA analogue)",
    "iqaudio-dac":               "IQaudIO DAC (RCA analogue)",
    "iqaudio-codec":             "IQaudIO Codec (audio in+out)",
    "justboom-dac":              "JustBoom DAC HAT (RCA analogue)",
    "justboom-digi":             "JustBoom Digi HAT (S/PDIF)",
    "justboom-amp":              "JustBoom Amp HAT (integrated amplifier)",
    "allo-boss-dac-pcm512x-audio": "Allo Boss DAC (RCA analogue)",
    "allo-digione":              "Allo DigiOne (S/PDIF coax)",
    "audioinjector-ultra":       "AudioInjector Ultra (multi-channel)",
    "fe-pi-audio":               "Fe-Pi Audio (RCA analogue)",
    "rpi-dac":                   "Raspberry Pi DAC Pro",
    "wm8960-soundcard":          "WM8960 Audio HAT",
    "pisound":                   "Blokas Pisound",
}


# ---------------------------------------------------------------------------
# System helpers
# ---------------------------------------------------------------------------

def _scan_overlay_dir() -> list[str]:
    """
    Glob the overlays directory for *.dtbo files whose name matches an
    audio pattern. Fast and reliable — reads filenames only, never the
    (binary) overlay contents.
    """
    d = _overlays_dir()
    names = []
    try:
        for f in sorted(Path(d).glob("*.dtbo")):
            if AUDIO_OVERLAY_RE.search(f.stem):
                names.append(f.stem)
    except Exception as e:
        print(f"[AudioConfig] overlay dir scan failed: {e}")
    return names


def _clean_overlay_desc(desc: str) -> str:
    """
    Tidy a README 'Info:' line for use as a dropdown label. Pi overlay
    descriptions almost always begin 'Configures the ...' / 'Configures a ...';
    strip that lead-in so the menu shows just the card name.
    """
    desc = desc.strip()
    desc = re.sub(r"^configures\s+(the|a|an)\s+", "", desc, flags=re.IGNORECASE)
    desc = re.sub(r"^configures\s+", "", desc, flags=re.IGNORECASE)
    return desc.strip()[:1].upper() + desc.strip()[1:] if desc.strip() else desc


def _scan_overlay_readme() -> dict[str, str]:
    """
    Parse the overlays README for audio overlays. The README is organised
    into blocks like:

        Name:   hifiberry-digi
        Info:   Configures the HifiBerry Digi and Digi+ audio cards
        Load:   dtoverlay=hifiberry-digi
        Params: ...

    Returns {overlay_name: description} for every block whose name or Info
    text matches an audio pattern. The descriptions are reused as friendly
    labels for overlays not already in OVERLAY_FRIENDLY.
    """
    readme = _overlay_readme()
    found: dict[str, str] = {}
    if not readme:
        return found

    try:
        with open(readme, encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
    except Exception as e:
        print(f"[AudioConfig] README read failed: {e}")
        return found

    current = None
    info = ""

    def _flush():
        if current and AUDIO_OVERLAY_RE.search(f"{current} {info}"):
            found[current] = _clean_overlay_desc(info)

    for raw in text.splitlines():
        m = re.match(r"\s*Name:\s*(\S+)", raw)
        if m:
            _flush()
            current = m.group(1)
            info = ""
            continue
        m = re.match(r"\s*Info:\s*(.*)", raw)
        if m and current:
            info = m.group(1).strip()
    _flush()
    return found


def _find_audio_overlays() -> list[str]:
    """
    Discover audio HAT overlays available on this system.

    Primary   : glob the overlays directory for *.dtbo files whose name
                matches an audio pattern.
    Secondary : parse the overlays README, which catches audio overlays
                whose filename lacks an obvious keyword and supplies a
                human-readable description used as a friendly label.
    Fallback  : a hardcoded list of common Pi audio overlays.
    """
    names = set(_scan_overlay_dir())

    for ov, desc in _scan_overlay_readme().items():
        names.add(ov)
        if desc:
            OVERLAY_FRIENDLY.setdefault(ov, desc)

    if names:
        return sorted(names)

    # Hardcoded fallback of common Pi audio overlays
    return [
        "hifiberry-dac",
        "hifiberry-dacplus",
        "hifiberry-dacplusadc",
        "hifiberry-dacplusadcpro",
        "hifiberry-digi",
        "hifiberry-digi-pro",
        "hifiberry-amp",
        "hifiberry-amp2",
        "iqaudio-dac",
        "iqaudio-dacplus",
        "iqaudio-codec",
        "justboom-dac",
        "justboom-digi",
        "rpi-dac",
        "googlevoicehat-soundcard",
        "pisound",
        "wm8960-soundcard",
    ]


def _read_config_txt() -> list[str]:
    try:
        with open(CONFIG_TXT, "r", encoding="utf-8") as f:
            return f.readlines()
    except Exception as e:
        print(f"[AudioConfig] Cannot read {CONFIG_TXT}: {e}")
        return []


def _write_config_txt(lines: list[str]) -> tuple[bool, str]:
    """
    Write config.txt. Tries three methods in order:
    1. Direct write (works if PulseBox is run with sudo)
    2. Write to /tmp then sudo cp (avoids tee stdin hang)
    3. Clear instructions if both fail
    """
    import tempfile
    content_str = "".join(lines)

    # Method 1: direct write
    try:
        with open(CONFIG_TXT, "w", encoding="utf-8") as f:
            f.write(content_str)
        return True, "config.txt updated successfully"
    except PermissionError:
        pass
    except Exception as e:
        return False, f"Write error: {e}"

    # Method 2: write to temp file, sudo cp (no stdin needed — no hang)
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                         delete=False,
                                         encoding="utf-8") as tmp:
            tmp.write(content_str)
            tmp_path = tmp.name

        result = subprocess.run(
            ["sudo", "cp", tmp_path, CONFIG_TXT],
            capture_output=True, text=True, timeout=8)
        os.unlink(tmp_path)

        if result.returncode == 0:
            return True, "config.txt updated (via sudo cp)"
        return False, (
            f"sudo cp failed: {result.stderr.strip()}\n\n"
            "To fix, run once in terminal:\n"
            "  echo 'pi ALL=(ALL) NOPASSWD: /usr/bin/cp' "
            "| sudo tee /etc/sudoers.d/pulsebox-config"
        )
    except subprocess.TimeoutExpired:
        return False, (
            "sudo timed out (needs NOPASSWD).\n"
            "Run in terminal:\n"
            "  echo 'pi ALL=(ALL) NOPASSWD: /usr/bin/cp' "
            "| sudo tee /etc/sudoers.d/pulsebox-config\n"
            "Or run PulseBox with: sudo python3 pulsebox.py"
        )
    except Exception as e:
        return False, str(e)


def _remove_managed_lines(lines: list[str]) -> list[str]:
    """Remove all lines previously written by this module."""
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if MANAGED_MARKER in line:
            # Skip this line and any immediately following managed content
            i += 1
            while i < len(lines) and lines[i].strip().startswith(
                    ("dtoverlay=", "hdmi_drive", "dtparam=audio",
                     "# hdmi", "# i2c", "# dtoverlay")):
                i += 1
            continue
        out.append(line)
        i += 1
    return out


def _get_current_audio_config() -> dict:
    """
    Parse config.txt and return what's currently configured.
    Returns dict with keys: mode ('i2c'|'hdmi'|'unknown'),
                            overlay (str), hdmi_port (1|2)
    """
    result = {"mode": "unknown", "overlay": "", "hdmi_port": 1}
    lines = _read_config_txt()
    for line in lines:
        s = line.strip()
        if s.startswith("dtoverlay=") and MANAGED_MARKER not in line:
            ov = s.split("=", 1)[1].split(",")[0]
            result["mode"] = "i2c"
            result["overlay"] = ov
        if MANAGED_MARKER in line:
            # Check what follows
            pass
    # Look for our managed block
    for i, line in enumerate(lines):
        if MANAGED_MARKER in line:
            for j in range(i + 1, min(i + 5, len(lines))):
                s = lines[j].strip()
                if s.startswith("dtoverlay="):
                    result["mode"] = "i2c"
                    result["overlay"] = s.split("=", 1)[1].split(",")[0]
                elif "hdmi" in s.lower() and "HDMI1" in line:
                    result["mode"] = "hdmi"
                    result["hdmi_port"] = 1
                elif "hdmi" in s.lower() and "HDMI2" in line:
                    result["mode"] = "hdmi"
                    result["hdmi_port"] = 2
    return result


def _apply_i2c_overlay(overlay_name: str) -> tuple[bool, str]:
    """
    Apply a full I2C audio HAT configuration:
      1. config.txt  — dtoverlay + i2c baudrate fix if needed
      2. /etc/asound.conf — ALSA default to hw:0,0
      3. WirePlumber config — priority sink rule
    """
    lines = _read_config_txt()
    lines = _remove_managed_lines(lines)

    # Add ,noaudio to any existing vc4-kms overlay line
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("dtoverlay=vc4-kms") and "noaudio" not in s:
            lines[i] = line.rstrip().rstrip("\n") + ",noaudio\n"

    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"

    block = [
        f"\n{MANAGED_MARKER} - I2C HAT: {overlay_name}\n",
        "dtparam=audio=off\n",
    ]
    # Apply i2c baudrate fix for wm8804-based overlays (Digi/Digi+ Pro)
    if overlay_name in I2C_FIX_OVERLAYS:
        block.append("dtparam=i2c_arm=on,i2c_arm_baudrate=50000\n")
    block.append(f"dtoverlay={overlay_name}\n")
    lines.extend(block)

    ok, msg = _write_config_txt(lines)
    if not ok:
        return ok, msg

    # Write /etc/asound.conf
    friendly = OVERLAY_FRIENDLY.get(overlay_name, overlay_name)
    asound_content = (
        f"# /etc/asound.conf -- written by PulseBox Audio Configurator\n"
        f"# HAT: {friendly}\n"
        f"pcm.!default {{\n"
        f"  type hw\n"
        f"  card 0\n"
        f"  device 0\n"
        f"}}\n\n"
        f"ctl.!default {{\n"
        f"  type hw\n"
        f"  card 0\n"
        f"}}\n"
    )
    asound_ok = _write_file_sudo(ASOUND_CONF, asound_content)

    # Write WirePlumber config
    wp_match = overlay_name.split("-")[0]   # e.g. hifiberry, justboom, allo
    wp_content = (
        f"# WirePlumber sink priority -- written by PulseBox Audio Configurator\n"
        f"# HAT: {friendly}\n"
        f"monitor.alsa.rules = [\n"
        f"  {{\n"
        f"    matches = [ {{ node.name = \"~alsa_output.*{wp_match}*\" }} ]\n"
        f"    actions = {{\n"
        f"      update-props = {{\n"
        f"        priority.session = 1100\n"
        f"        node.description = \"{friendly}\"\n"
        f"      }}\n"
        f"    }}\n"
        f"  }}\n"
        f"]\n"
    )
    try:
        os.makedirs(WP_CONF_DIR, exist_ok=True)
        with open(WP_CONF_FILE, "w") as f:
            f.write(wp_content)
        wp_ok = True
    except Exception as e:
        print(f"[AudioConfig] WirePlumber write failed: {e}")
        wp_ok = False

    parts = [msg]
    if asound_ok:
        parts.append("asound.conf written")
    else:
        parts.append("asound.conf: needs sudo (run fix_hifiberry_audio.sh)")
    if wp_ok:
        parts.append("WirePlumber config written")

    # HiFiBerry Digi/Digi+ Pro: the wm8804's "Tx Source" mixer control
    # defaults to "S/PDIF RX" (passthrough) on every boot instead of "AIF"
    # (audio from the Pi). This produces laser light but no audio.
    # Install a oneshot systemd service to set it correctly on every boot.
    if overlay_name in I2C_FIX_OVERLAYS:
        tx_ok = _install_tx_source_service()
        if tx_ok:
            parts.append("Tx Source boot fix installed")
        else:
            parts.append("Tx Source fix needs sudo — see notes")

    return True, " | ".join(parts)


def _install_tx_source_service() -> bool:
    """Install a systemd oneshot service that sets the wm8804 Tx Source
    to 'AIF' on every boot. Without this, the HiFiBerry Digi defaults to
    'S/PDIF RX' (passthrough) — laser light is on but no audio plays."""
    service_content = """[Unit]
Description=Set HiFiBerry Digi Tx Source to AIF
After=sound.target
Wants=sound.target

[Service]
Type=oneshot
ExecStart=/usr/bin/amixer -c 0 cset numid=1 1
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""
    service_path = "/etc/systemd/system/hifiberry-tx-source.service"

    if not _write_file_sudo(service_path, service_content):
        return False

    try:
        subprocess.run(["sudo", "-n", "systemctl", "daemon-reload"],
                       capture_output=True, timeout=10)
        subprocess.run(["sudo", "-n", "systemctl", "enable",
                        "hifiberry-tx-source.service"],
                       capture_output=True, timeout=10)
        # Apply immediately too, so it works without a reboot
        subprocess.run(["amixer", "-c", "0", "cset", "numid=1", "1"],
                       capture_output=True, timeout=5)
        return True
    except Exception as e:
        print(f"[AudioConfig] Tx Source service enable failed: {e}")
        return False


def _write_file_sudo(path: str, content: str) -> bool:
    """Write a file that needs root.
    Strategy:
      1. Try direct write (works if running as root or file is writable)
      2. Try pkexec cp — shows a GUI polkit password prompt
      3. Try sudo -n (non-interactive) — works if NOPASSWD sudoers rule exists
      4. Fall back gracefully with a clear message
    """
    import tempfile

    # Write content to a temp file first
    try:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".conf", delete=False)
        tmp.write(content)
        tmp_path = tmp.name
        tmp.close()
    except Exception as e:
        print(f"[AudioConfig] Cannot create temp file: {e}")
        return False

    # Method 1: direct write (no root needed)
    try:
        with open(path, "w") as f:
            f.write(content)
        os.unlink(tmp_path)
        return True
    except PermissionError:
        pass
    except Exception as e:
        print(f"[AudioConfig] Direct write failed: {e}")

    # Method 2: sudo -n tee (non-interactive — works with NOPASSWD sudoers rule
    # installed by install.sh. tee reads stdin so no cp path ambiguity.)
    try:
        result = subprocess.run(
            ["sudo", "-n", "tee", path],
            input=content, capture_output=True,
            text=True, timeout=8)
        if result.returncode == 0:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            print(f"[AudioConfig] Wrote {path} via sudo tee")
            return True
    except Exception as e:
        print(f"[AudioConfig] sudo tee failed: {e}")

    # Method 3: sudo -n cp (fallback)
    try:
        result = subprocess.run(
            ["sudo", "-n", "cp", tmp_path, path],
            capture_output=True, timeout=8)
        if result.returncode == 0:
            os.unlink(tmp_path)
            print(f"[AudioConfig] Wrote {path} via sudo cp")
            return True
    except Exception as e:
        print(f"[AudioConfig] sudo cp failed: {e}")

    # Method 4: pkexec — GUI polkit prompt (last resort)
    try:
        result = subprocess.run(
            ["pkexec", "cp", tmp_path, path],
            capture_output=True, timeout=30)
        if result.returncode == 0:
            os.unlink(tmp_path)
            print(f"[AudioConfig] Wrote {path} via pkexec")
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    except Exception as e:
        print(f"[AudioConfig] pkexec failed: {e}")

    # All methods failed
    try:
        os.unlink(tmp_path)
    except Exception:
        pass
    print(f"[AudioConfig] Could not write {path} — no root access available")
    return False


def _apply_hdmi_output(port: int) -> tuple[bool, str]:
    """
    Configure HDMI audio output for port 1 or 2.
    Removes ,noaudio from vc4-kms overlay so HDMI audio is available.
    """
    lines = _read_config_txt()
    lines = _remove_managed_lines(lines)

    # Remove ,noaudio from any vc4-kms overlay line (HDMI needs audio)
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("dtoverlay=vc4-kms") and "noaudio" in s:
            lines[i] = line.replace(",noaudio", "")

    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"

    port_index = port - 1   # 0-based for config.txt
    block = [
        f"\n{MANAGED_MARKER} - HDMI{port}\n",
        "dtparam=audio=on\n",
        f"hdmi_drive:{port_index}=2\n",
        f"hdmi_force_hotplug:{port_index}=1\n",
    ]
    lines.extend(block)
    return _write_config_txt(lines)


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------

class AudioOutputConfigurator(JukeboxModule):
    MODULE_ID          = "audio_output_configurator"
    MODULE_NAME        = "Audio Output Configurator"
    MODULE_VERSION = APP_VERSION
    MODULE_DESCRIPTION = ("Configure audio output: I2C HAT overlay or HDMI1/HDMI2. "
                          "Edits /boot/firmware/config.txt. Reboot required.")
    MODULE_AUTHOR      = "Andrew J. Morgan"

    def __init__(self):
        super().__init__()
        self._mode_var:    tk.StringVar | None = None   # "i2c" or "hdmi"
        self._overlay_var: tk.StringVar | None = None
        self._hdmi_var:    tk.StringVar | None = None
        self._status_var:  tk.StringVar | None = None
        self._overlay_combo:  ttk.Combobox | None = None
        self._hdmi_combo:     ttk.Combobox | None = None
        self._overlay_frame:  tk.Frame | None = None
        self._hdmi_frame:     tk.Frame | None = None
        self._overlays:    list[str] = []
        self._scanning     = False

    def on_load(self, app):
        super().on_load(app)
        # Scan overlays in background
        threading.Thread(target=self._scan_overlays, daemon=True).start()

    def _scan_overlays(self):
        self._overlays = _find_audio_overlays()
        if self._overlay_combo and self.app:
            self.app.root.after(0, self._populate_overlay_combo)

    def _populate_overlay_combo(self):
        if self._overlay_combo:
            # Show friendly names where available, raw overlay name otherwise
            display = [OVERLAY_FRIENDLY.get(o, o) for o in self._overlays]
            self._overlay_combo["values"] = display
            self._overlay_display_to_raw = {
                OVERLAY_FRIENDLY.get(o, o): o for o in self._overlays
            }
            current = _get_current_audio_config()
            cur_friendly = OVERLAY_FRIENDLY.get(current["overlay"], current["overlay"])
            if cur_friendly in display:
                self._overlay_var.set(cur_friendly)
            elif display:
                self._overlay_var.set(display[0])

    # ------------------------------------------------------------------
    # UI Panel
    # ------------------------------------------------------------------

    def get_ui_panel(self, parent) -> tk.Frame:
        from ui.theme import (BG_PANEL, BG_CARD, BG_DARK, ACCENT, NEON_BLUE,
                              WARNING, SUCCESS, DANGER, TEXT_PRIMARY,
                              TEXT_SECONDARY, TEXT_DIM,
                              FONT_BODY, FONT_SMALL, FONT_TINY,
                              FONT_SUBHEAD, PAD, PAD_SM)

        frame = tk.Frame(parent, bg=BG_PANEL)

        # ── Current config display ────────────────────────────────────
        current = _get_current_audio_config()
        cur_text = f"Current: {current['mode'].upper()}"
        if current["overlay"]:
            cur_text += f" — {current['overlay']}"
        elif current["mode"] == "hdmi":
            cur_text += f"{current['hdmi_port']}"

        tk.Label(frame, text=cur_text, font=FONT_SMALL,
                 fg=NEON_BLUE, bg=BG_PANEL).pack(anchor="w", pady=(0, 8))

        # ── Three always-visible output type buttons ─────────────────
        # i2c = I2S DAC, hdmi1 = HDMI port 1, hdmi2 = HDMI port 2
        self._mode_var = tk.StringVar(value="i2c")

        mode_row = tk.Frame(frame, bg=BG_PANEL)
        mode_row.pack(fill="x", pady=(0, 8))
        tk.Label(mode_row, text="Output Type:", font=FONT_BODY,
                 fg=TEXT_PRIMARY, bg=BG_PANEL,
                 width=14, anchor="w").pack(side="left")

        for label, value, colour in [
            ("I2S / DAC", "i2c",   ACCENT),
            ("HDMI 1",    "hdmi1", NEON_BLUE),
            ("HDMI 2",    "hdmi2", "#ff6b35"),
        ]:
            tk.Radiobutton(
                mode_row, text=label,
                variable=self._mode_var, value=value,
                font=FONT_BODY, fg=colour,
                bg=BG_PANEL, activebackground=BG_PANEL,
                selectcolor=BG_CARD, cursor="hand2",
                command=self._on_mode_change,
            ).pack(side="left", padx=(0, 16))

        # HAT overlay — only relevant for I2S mode, always visible
        self._overlay_frame = tk.Frame(frame, bg=BG_PANEL)
        self._overlay_frame.pack(fill="x", pady=2)

        tk.Label(self._overlay_frame, text="HAT Overlay:",
                 font=FONT_BODY, fg=TEXT_PRIMARY,
                 bg=BG_PANEL, width=14, anchor="w").pack(side="left")

        self._overlay_var = tk.StringVar(value="Scanning…")
        self._overlay_combo = ttk.Combobox(
            self._overlay_frame,
            textvariable=self._overlay_var,
            state="readonly",
            font=FONT_BODY,
            width=32,
        )
        self._overlay_combo["values"] = ["Scanning overlays…"]
        self._overlay_combo.pack(side="left", padx=(0, 8), ipady=3)

        tk.Button(
            self._overlay_frame, text="⟳ Rescan",
            font=FONT_TINY, fg=ACCENT, bg=BG_CARD,
            relief="flat", bd=0, cursor="hand2", padx=6, pady=3,
            command=self._rescan_overlays
        ).pack(side="left")

        tk.Label(self._overlay_frame,
                 text="Overlays: grep -ril dac/audio/digi/amp /boot/overlays/",
                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_PANEL).pack(
            anchor="w", pady=(2, 0))

        # No separate _hdmi_frame/_hdmi_var — port is encoded in _mode_var
        self._hdmi_frame = None
        self._hdmi_var   = None

        # ── Status line ───────────────────────────────────────────────
        self._status_var = tk.StringVar(value="")
        tk.Label(frame, textvariable=self._status_var,
                 font=FONT_SMALL, fg=SUCCESS,
                 bg=BG_PANEL, wraplength=500,
                 justify="left", anchor="w").pack(
            fill="x", pady=(8, 4))

        # ── Preview of what will be written ──────────────────────────
        tk.Label(frame, text="Will add to config.txt:",
                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_PANEL).pack(anchor="w")

        self._preview_var = tk.StringVar()
        tk.Label(frame, textvariable=self._preview_var,
                 font=("Courier", 9), fg=NEON_BLUE,
                 bg="#0a0a12", padx=8, pady=4,
                 justify="left", anchor="w").pack(
            fill="x", pady=(0, 8))

        self._overlay_var.trace_add("write", lambda *_: self._update_preview())
        # _hdmi_var removed — port encoded in _mode_var
        self._update_preview()

        # ── Apply button ──────────────────────────────────────────────
        btn_row = tk.Frame(frame, bg=BG_PANEL)
        btn_row.pack(fill="x", pady=(4, 0))

        tk.Button(
            btn_row,
            text="  Apply & Save config.txt  ",
            font=FONT_SUBHEAD, fg=BG_PANEL, bg=ACCENT,
            relief="flat", bd=0, cursor="hand2",
            padx=16, pady=8,
            command=self._apply
        ).pack(side="left")

        tk.Label(btn_row,
                 text="⚠  Reboot required after applying",
                 font=FONT_TINY, fg=WARNING,
                 bg=BG_PANEL).pack(side="left", padx=12)

        tk.Button(
            btn_row,
            text="Reboot Now",
            font=FONT_SMALL, fg=DANGER, bg=BG_CARD,
            relief="flat", bd=0, cursor="hand2",
            padx=10, pady=8,
            command=self._reboot
        ).pack(side="right")

        # ── config.txt viewer ────────────────────────────────────────
        tk.Label(frame, text=f"Current {CONFIG_TXT}:",
                 font=FONT_TINY, fg=TEXT_PRIMARY,
                 bg=BG_PANEL).pack(anchor="w", pady=(12, 2))

        cfg_frame = tk.Frame(frame, bg=BG_DARK)
        cfg_frame.pack(fill="x")

        self._cfg_text = tk.Text(
            cfg_frame, height=10,
            font=("Courier", 8),
            bg="#0a0a0a", fg="#aaaacc",
            state="disabled", relief="flat", wrap="none")
        self._cfg_text.pack(side="left", fill="both", expand=True)

        sb = tk.Scrollbar(cfg_frame, command=self._cfg_text.yview,
                          bg=BG_DARK, troughcolor=BG_DARK)
        sb.pack(side="right", fill="y")
        self._cfg_text.config(yscrollcommand=sb.set)

        self._load_config_preview()

        # Populate overlays if already scanned
        if self._overlays:
            self._populate_overlay_combo()

        return frame

    # ------------------------------------------------------------------
    # Mode switch
    # ------------------------------------------------------------------

    def _on_mode_change(self):
        self._update_preview()

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------

    def _update_preview(self):
        if not self._preview_var:
            return
        mode = self._mode_var.get() if self._mode_var else "i2c"
        if mode == "i2c":
            display = self._overlay_var.get() if self._overlay_var else ""
            ov = getattr(self, "_overlay_display_to_raw", {}).get(display, display)
            i2c_line = ("dtparam=i2c_arm=on,i2c_arm_baudrate=50000  "
                        "<-- wm8804 fix\n" if ov in I2C_FIX_OVERLAYS else "")
            text = (f"config.txt:\n"
                    f"  {MANAGED_MARKER} - I2C HAT: {ov}\n"
                    f"  dtparam=audio=off\n"
                    f"  {i2c_line}"
                    f"  dtoverlay={ov}\n"
                    f"\nasound.conf: pcm/ctl default -> hw:0,0\n"
                    f"WirePlumber: priority sink rule for {ov.split(chr(45))[0] if ov else '...'}")
        else:
            port = "HDMI2" if mode == "hdmi2" else "HDMI1"
            idx  = 1 if mode == "hdmi2" else 0
            text = (f"config.txt:\n"
                    f"  {MANAGED_MARKER} - {port}\n"
                    f"  dtparam=audio=on\n"
                    f"  hdmi_drive:{idx}=2\n"
                    f"  hdmi_force_hotplug:{idx}=1")
        self._preview_var.set(text)

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    def _apply(self):
        mode = self._mode_var.get()
        if mode == "i2c":
            display_name = self._overlay_var.get()
            if not display_name or "Scanning" in display_name:
                self._set_status("Please select an overlay first", error=True)
                return
            # Map friendly display name back to raw overlay string
            overlay = getattr(self, "_overlay_display_to_raw", {}).get(
                display_name, display_name)
            ok, msg = _apply_i2c_overlay(overlay)
        elif mode in ("hdmi1", "hdmi2"):
            port = 2 if mode == "hdmi2" else 1
            ok, msg = _apply_hdmi_output(port)
        else:
            self._set_status("Unknown output mode", error=True)
            return

        self._set_status(msg, error=not ok)
        if ok:
            self.config.set("audio_output_mode", mode)
            self.config.set("audio_output_value",
                            self._overlay_var.get() if mode == "i2c"
                            else mode)
            self._load_config_preview()
            self.emit_notification(
                f"Audio output updated. Reboot to apply.", "success")

    def _set_status(self, msg: str, error: bool = False):
        from ui.theme import SUCCESS, DANGER
        if self._status_var and self.app:
            self.app.root.after(
                0, lambda: self._status_var.set(msg))
            # Change label colour — rebuild label isn't easy so use tag trick
        print(f"[AudioConfig] {'ERROR' if error else 'OK'}: {msg}")

    # ------------------------------------------------------------------
    # config.txt viewer
    # ------------------------------------------------------------------

    def _load_config_preview(self):
        if not hasattr(self, '_cfg_text'):
            return
        lines = _read_config_txt()
        self._cfg_text.config(state="normal")
        self._cfg_text.delete("1.0", "end")
        if lines:
            self._cfg_text.insert("end", "".join(lines))
            # Highlight our managed lines
            content = "".join(lines)
            for i, line in enumerate(lines, start=1):
                if MANAGED_MARKER in line:
                    self._cfg_text.tag_add("managed",
                                           f"{i}.0", f"{i}.end")
            self._cfg_text.tag_config("managed",
                                      foreground="#e8b004",
                                      background="#1a1600")
        else:
            self._cfg_text.insert("end",
                                  f"Cannot read {CONFIG_TXT}\n"
                                  "(may need sudo)")
        self._cfg_text.config(state="disabled")

    # ------------------------------------------------------------------
    # Overlay rescan
    # ------------------------------------------------------------------

    def _rescan_overlays(self):
        if self._overlay_combo:
            self._overlay_combo["values"] = ["Scanning…"]
            self._overlay_var.set("Scanning…")
        threading.Thread(target=self._scan_overlays, daemon=True).start()

    # ------------------------------------------------------------------
    # Reboot
    # ------------------------------------------------------------------

    def _reboot(self):
        if messagebox.askyesno(
                "Reboot",
                "Reboot the Pi now to apply audio changes?",
                parent=self.app.root):
            import threading
            def _do_reboot():
                import time
                time.sleep(0.5)   # let UI close dialog first
                try:
                    # Try systemctl reboot first (works without sudo on Pi OS)
                    result = subprocess.run(
                        ["systemctl", "reboot"],
                        capture_output=True, timeout=5)
                    if result.returncode != 0:
                        subprocess.run(["sudo", "reboot"],
                                       capture_output=True, timeout=5)
                except Exception as e:
                    if self.app:
                        self.app.root.after(0, lambda: messagebox.showerror(
                            "Reboot failed",
                            f"{e}\n\nRun manually: sudo reboot",
                            parent=self.app.root))
            threading.Thread(target=_do_reboot, daemon=True).start()
