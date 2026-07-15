"""
PulseBox UI Theme — auto-scales to screen resolution.

Base design is 1280×800 (Pi 7" official display / HD ready).
On larger screens everything scales up proportionally so 6 album
covers still fit per row but are bigger.

Call init_scale(root) once after tk.Tk() is created.
All size constants are then set for the detected resolution.
"""

import tkinter as tk

# ---------------------------------------------------------------------------
# Colours  (never scaled)
# ---------------------------------------------------------------------------
BG_DARK       = "#0a0a0f"
BG_PANEL      = "#12121c"
BG_CARD       = "#1a1a2e"
BG_CARD_HOVER = "#232342"
ACCENT        = "#e8b004"
ACCENT2       = "#ff6b35"
NEON_BLUE     = "#00d4ff"
NEON_PINK     = "#ff2d78"
TEXT_PRIMARY  = "#f0f0f0"
TEXT_SECONDARY= "#8888aa"
TEXT_DIM      = "#44445a"
BORDER        = "#2a2a40"
BORDER_ACCENT = "#3a3a60"
SUCCESS       = "#00cc88"
WARNING       = "#ffaa00"
DANGER        = "#ff3355"

STATUS_COLORS = {
    "playing": NEON_BLUE,
    "queued":  ACCENT,
    "default": TEXT_SECONDARY,
}

# ---------------------------------------------------------------------------
# Base sizes  (designed for 1280×800)
# ---------------------------------------------------------------------------
_BASE_W  = 1280
_BASE_H  = 800

_BASE = {
    "PLAYER_H": 88,
    "CARD_W":   120,
    "CARD_H":   150,
    "ART_SIZE": 96,
    "PAD":       12,
    "PAD_SM":     6,
    "NAV_W":    180,
    # Fonts (sizes)
    "F_DISPLAY": 28,
    "F_HEADING": 16,
    "F_SUBHEAD": 13,
    "F_BODY":    11,
    "F_SMALL":    9,
    "F_TINY":     8,
    "F_AZ":      14,
    "F_TRACK":   10,
    "F_TRACK_SM": 8,
}

# Will be populated by init_scale()
_scale = 1.0

# ---------------------------------------------------------------------------
# Scaled constants (module-level, imported with *)
# ---------------------------------------------------------------------------

def _s(key: str) -> int:
    """Return a base size scaled to current resolution, minimum 1."""
    return max(1, int(_BASE[key] * _scale))


# Defaults at base resolution — overwritten by init_scale()
PLAYER_H  = _BASE["PLAYER_H"]
CARD_W    = _BASE["CARD_W"]
CARD_H    = _BASE["CARD_H"]
ART_SIZE  = _BASE["ART_SIZE"]
PAD       = _BASE["PAD"]
PAD_SM    = _BASE["PAD_SM"]
NAV_W     = _BASE["NAV_W"]
AZ_BTN_W  = 42
AZ_BTN_H  = 42
CORNER    = 8

FONT_FAMILY  = "Helvetica"
FONT_DISPLAY = ("Helvetica", _BASE["F_DISPLAY"], "bold")
FONT_HEADING = ("Helvetica", _BASE["F_HEADING"], "bold")
FONT_SUBHEAD = ("Helvetica", _BASE["F_SUBHEAD"], "bold")
FONT_BODY    = ("Helvetica", _BASE["F_BODY"])
FONT_SMALL   = ("Helvetica", _BASE["F_SMALL"])
FONT_TINY    = ("Helvetica", _BASE["F_TINY"])
FONT_AZ      = ("Helvetica", _BASE["F_AZ"], "bold")
FONT_TRACK   = ("Helvetica", _BASE["F_TRACK"])
FONT_TRACK_SM= ("Helvetica", _BASE["F_TRACK_SM"])


# ---------------------------------------------------------------------------
# Early scale detection — runs at import time using a temporary hidden Tk
# root, BEFORE any page module does `from .theme import *`.
#
# This is necessary because `from .theme import *` copies these names into
# the importing module's namespace at import time. If init_scale() ran later
# (after pulsebox.py imports all page modules), those modules would already
# have captured the unscaled 1280x800 base values and the later mutation of
# theme.PLAYER_H etc. would have no effect on them.
#
# By detecting and applying scale here — at theme.py's own import time, which
# happens before pulsebox.py imports any page module — every page module's
# `from .theme import *` picks up the already-scaled values.
# ---------------------------------------------------------------------------

def _early_scale_detect():
    global _scale
    global PLAYER_H, CARD_W, CARD_H, ART_SIZE, PAD, PAD_SM, NAV_W
    global AZ_BTN_W, AZ_BTN_H, CORNER
    global FONT_DISPLAY, FONT_HEADING, FONT_SUBHEAD, FONT_BODY
    global FONT_SMALL, FONT_TINY, FONT_AZ, FONT_TRACK, FONT_TRACK_SM

    try:
        # Temporary hidden root purely to query screen resolution
        _tmp = tk.Tk()
        _tmp.withdraw()
        sw = _tmp.winfo_screenwidth()
        sh = _tmp.winfo_screenheight()
        _tmp.destroy()
    except Exception as e:
        print(f"[Theme] Early scale detect failed ({e}) — using 1.0x")
        return

    scale_w = sw / _BASE_W
    scale_h = sh / _BASE_H
    _scale  = max(0.7, min(min(scale_w, scale_h), 2.5))

    print(f"[Theme] Detected screen {sw}x{sh} -> scale {_scale:.2f}x")

    PLAYER_H  = _s("PLAYER_H")
    CARD_W    = _s("CARD_W")
    CARD_H    = _s("CARD_H")
    ART_SIZE  = _s("ART_SIZE")
    PAD       = _s("PAD")
    PAD_SM    = _s("PAD_SM")
    NAV_W     = _s("NAV_W")
    AZ_BTN_W  = max(32, int(42 * _scale))
    AZ_BTN_H  = max(32, int(42 * _scale))
    CORNER    = max(4,  int(8  * _scale))

    def _f(key, style=""):
        size = max(7, int(_BASE[key] * _scale))
        return ("Helvetica", size, style) if style else ("Helvetica", size)

    FONT_DISPLAY  = _f("F_DISPLAY",  "bold")
    FONT_HEADING  = _f("F_HEADING",  "bold")
    FONT_SUBHEAD  = _f("F_SUBHEAD",  "bold")
    FONT_BODY     = _f("F_BODY")
    FONT_SMALL    = _f("F_SMALL")
    FONT_TINY     = _f("F_TINY")
    FONT_AZ       = _f("F_AZ",       "bold")
    FONT_TRACK    = _f("F_TRACK")
    FONT_TRACK_SM = _f("F_TRACK_SM")


_early_scale_detect()


# ---------------------------------------------------------------------------
# init_scale — call once after Tk root is created
# ---------------------------------------------------------------------------

def init_scale(root: tk.Tk):
    """
    Detect screen resolution and update all size/font constants.
    Called from pulsebox.py immediately after tk.Tk() is created.
    """
    global _scale
    global PLAYER_H, CARD_W, CARD_H, ART_SIZE, PAD, PAD_SM, NAV_W
    global AZ_BTN_W, AZ_BTN_H, CORNER
    global FONT_DISPLAY, FONT_HEADING, FONT_SUBHEAD, FONT_BODY
    global FONT_SMALL, FONT_TINY, FONT_AZ, FONT_TRACK, FONT_TRACK_SM

    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()

    # Scale based on the smaller axis ratio to avoid overshooting
    scale_w = sw / _BASE_W
    scale_h = sh / _BASE_H
    _scale  = min(scale_w, scale_h)

    # Clamp: never scale below 0.7 or above 2.5
    _scale = max(0.7, min(_scale, 2.5))

    print(f"[Theme] Screen {sw}×{sh}  →  scale {_scale:.2f}x")

    # Sizes
    PLAYER_H  = _s("PLAYER_H")
    CARD_W    = _s("CARD_W")
    CARD_H    = _s("CARD_H")
    ART_SIZE  = _s("ART_SIZE")
    PAD       = _s("PAD")
    PAD_SM    = _s("PAD_SM")
    NAV_W     = _s("NAV_W")
    AZ_BTN_W  = max(32, int(42 * _scale))
    AZ_BTN_H  = max(32, int(42 * _scale))
    CORNER    = max(4,  int(8  * _scale))

    # Fonts
    def _f(key, style=""):
        size = max(7, int(_BASE[key] * _scale))
        if style:
            return ("Helvetica", size, style)
        return ("Helvetica", size)

    FONT_DISPLAY  = _f("F_DISPLAY",  "bold")
    FONT_HEADING  = _f("F_HEADING",  "bold")
    FONT_SUBHEAD  = _f("F_SUBHEAD",  "bold")
    FONT_BODY     = _f("F_BODY")
    FONT_SMALL    = _f("F_SMALL")
    FONT_TINY     = _f("F_TINY")
    FONT_AZ       = _f("F_AZ",       "bold")
    FONT_TRACK    = _f("F_TRACK")
    FONT_TRACK_SM = _f("F_TRACK_SM")


def get_scale() -> float:
    return _scale


def scaled(value: int) -> int:
    """Scale an arbitrary pixel value."""
    return max(1, int(value * _scale))
