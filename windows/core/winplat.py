"""
PulseBox — Windows platform helpers
===================================
Everything the Pi build did by shelling out to Linux tools (aplay, eject,
cdparanoia, /proc/mounts, sudo shutdown …) lives here as a small Windows
equivalent, so the rest of the app has one place to ask.

Uses only the standard library (ctypes / winreg / subprocess). Every function
degrades to an empty/False result on a non-Windows machine, which is what lets
the UI be smoke-tested elsewhere.
"""

import os
import shutil
import string
import subprocess
import sys

IS_WINDOWS = sys.platform == "win32"

# Stops a console window flashing up each time a helper .exe is launched from
# the windowed (pythonw / compiled) app.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_DRIVE_REMOTE = 4
_DRIVE_CDROM  = 5


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def enable_dpi_awareness() -> None:
    """
    Tell Windows this process handles its own scaling. Without it, on a
    display set to 125%/150% Windows bitmap-stretches the whole Tk window
    (blurry) and reports a reduced screen size, which throws off the theme's
    resolution-based scaling. Must run before the first tk.Tk().
    """
    if not IS_WINDOWS:
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)     # Windows 8.1+
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()      # older fallback
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Drives
# ---------------------------------------------------------------------------

def _drives_of_type(drive_type: int) -> list[str]:
    """Drive roots like 'D:' whose GetDriveType matches."""
    if not IS_WINDOWS:
        return []
    import ctypes
    k32 = ctypes.windll.kernel32
    mask = k32.GetLogicalDrives()
    found = []
    for i, letter in enumerate(string.ascii_uppercase):
        if mask & (1 << i):
            if k32.GetDriveTypeW(f"{letter}:\\") == drive_type:
                found.append(f"{letter}:")
    return found


def optical_drives() -> list[str]:
    """All CD/DVD drives, e.g. ['D:']. Empty if there are none."""
    return _drives_of_type(_DRIVE_CDROM)


def first_optical_drive() -> "str | None":
    drives = optical_drives()
    return drives[0] if drives else None


def volume_label(drive: str) -> str:
    """Volume label of a drive ('' if no disc / unreadable)."""
    if not IS_WINDOWS or not drive:
        return ""
    import ctypes
    buf = ctypes.create_unicode_buffer(261)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(
        drive.rstrip("\\") + "\\", buf, 261, None, None, None, None, 0)
    return buf.value if ok else ""


def network_drives() -> list[tuple[str, str]]:
    """
    Mapped network drives as [(path, label)], e.g. ('Z:\\', 'Z:  \\\\nas\\music').
    """
    if not IS_WINDOWS:
        return []
    import ctypes
    out = []
    for d in _drives_of_type(_DRIVE_REMOTE):
        remote = ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.c_ulong(1024)
            if ctypes.windll.mpr.WNetGetConnectionW(
                    d, buf, ctypes.byref(size)) == 0:
                remote = buf.value
        except Exception:
            pass
        out.append((d + "\\", f"{d}  {remote}".rstrip()))
    return out


def normalise_share(share: str) -> str:
    """Accept //server/share, smb://server/share or \\\\server\\share."""
    s = share.strip().replace("/", "\\")
    if s.lower().startswith("smb:"):
        s = s[4:]
    return "\\\\" + s.lstrip("\\").rstrip("\\")


def _free_drive_letter() -> "str | None":
    if not IS_WINDOWS:
        return None
    import ctypes
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    for letter in "ZYXWVUTSRQPONMLKJIHGFE":
        if not mask & (1 << (ord(letter) - ord("A"))):
            return f"{letter}:"
    return None


def map_network_share(share: str, username: str = "", password: str = "",
                      persist: bool = True) -> "tuple[bool, str, str]":
    """
    Map an SMB share to the next free drive letter with `net use`.
    Returns (ok, message, path). Credentials are handed to Windows; PulseBox
    stores nothing itself.
    """
    if not IS_WINDOWS:
        return False, "Only available on Windows", ""
    unc = normalise_share(share)
    if unc.count("\\") < 3 or not unc.split("\\")[-1]:
        return False, r"Enter the share as \\server\share", ""
    letter = _free_drive_letter()
    if not letter:
        return False, "No free drive letter", ""
    cmd = ["net", "use", letter, unc]
    if username:
        cmd += [password, f"/user:{username}"]
    cmd.append("/persistent:yes" if persist else "/persistent:no")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=40,
                           stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    except Exception as e:
        return False, str(e), ""
    if r.returncode == 0:
        return True, f"Connected {unc} as {letter}", letter + "\\"
    err = " ".join((r.stderr or r.stdout).split()) or f"net use exit {r.returncode}"
    return False, err, ""


def unmap_network_drive(path: str) -> "tuple[bool, str]":
    if not IS_WINDOWS:
        return False, "Only available on Windows"
    letter = path[:2]
    try:
        r = subprocess.run(["net", "use", letter, "/delete", "/y"],
                           capture_output=True, text=True, timeout=30,
                           stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    except Exception as e:
        return False, str(e)
    if r.returncode == 0:
        return True, f"Disconnected {letter}"
    return False, " ".join((r.stderr or r.stdout).split()) or "net use failed"


# ---------------------------------------------------------------------------
# Optical disc control via MCI (winmm) — replaces cdparanoia + eject
# ---------------------------------------------------------------------------

def _mci(command: str) -> "tuple[int, str]":
    import ctypes
    buf = ctypes.create_unicode_buffer(255)
    err = ctypes.windll.winmm.mciSendStringW(command, buf, 254, 0)
    return err, buf.value


def cd_audio_track_count(drive: str) -> int:
    """Number of audio tracks on the disc in `drive` ('D:'); 0 if none."""
    if not IS_WINDOWS or not drive:
        return 0
    alias = "pbcd"
    try:
        err, _ = _mci(f"open {drive[:2]} type cdaudio alias {alias} shareable")
        if err:
            return 0
        try:
            err, present = _mci(f"status {alias} media present")
            if err or present.strip().lower() != "true":
                return 0
            err, total = _mci(f"status {alias} number of tracks")
            if err:
                return 0
            count = 0
            for n in range(1, int(total) + 1):
                err, kind = _mci(f"status {alias} type track {n}")
                if not err and kind.strip().lower() == "audio":
                    count += 1
            return count
        finally:
            _mci(f"close {alias}")
    except Exception:
        return 0


def eject(drive: str) -> bool:
    """Open the tray of an optical drive."""
    if not IS_WINDOWS or not drive:
        return False
    alias = "pbej"
    try:
        err, _ = _mci(f"open {drive[:2]} type cdaudio alias {alias} shareable")
        if err:
            return False
        try:
            err, _ = _mci(f"set {alias} door open")
            return err == 0
        finally:
            _mci(f"close {alias}")
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Finding helper programs (VLC, MakeMKV, HandBrakeCLI, ffmpeg)
# ---------------------------------------------------------------------------

_KNOWN_DIRS = {
    "vlc":          [r"VideoLAN\VLC"],
    "makemkvcon64": [r"MakeMKV"],
    "makemkvcon":   [r"MakeMKV"],
    "HandBrakeCLI": [r"HandBrake", r"HandBrakeCLI"],
    "ffmpeg":       [r"ffmpeg\bin"],
}


def find_exe(name: str) -> str:
    """
    Full path to a helper program, or ''. Looks on PATH first, then next to
    PulseBox (a tools\\ folder), then the usual Program Files locations —
    Windows installers rarely add themselves to PATH.
    """
    p = shutil.which(name)
    if p:
        return p
    exe = name if name.lower().endswith(".exe") else name + ".exe"
    roots = []
    base = os.environ.get("PULSEBOX_DIR") or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))
    roots.append(os.path.join(base, "tools"))
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        pf = os.environ.get(var)
        if pf:
            for sub in _KNOWN_DIRS.get(name, []):
                roots.append(os.path.join(pf, sub))
    for root in roots:
        cand = os.path.join(root, exe)
        if os.path.isfile(cand):
            return cand
    return ""


# ---------------------------------------------------------------------------
# Power
# ---------------------------------------------------------------------------

def system_power(action: str) -> None:
    """action: 'shutdown' or 'reboot'. Raises on failure."""
    if not IS_WINDOWS:
        raise RuntimeError("Only available on Windows")
    flag = "/r" if action == "reboot" else "/s"
    subprocess.run(["shutdown", flag, "/t", "0"], timeout=10, check=True,
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
