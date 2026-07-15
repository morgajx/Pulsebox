"""
PulseBox About Page
Shows application info, authors, licence and third-party credits.
"""

import tkinter as tk
from .theme import *
from .widgets import ScrollableFrame

from core.config import APP_VERSION
APP_YEAR    = "2025"

LICENCE_TEXT = """\
PulseBox is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation, either version 2 of the License, or (at your option) any later
version.

PulseBox is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
PulseBox. If not, see <https://www.gnu.org/licenses/>.

This program uses components distributed under their own licences.
See the Credits section below for details.
"""

CREDITS = [
    {
        "name":    "Python",
        "version": "3.x",
        "role":    "Programming language and standard library",
        "licence": "Python Software Foundation Licence (PSF)",
        "url":     "https://www.python.org",
        "author":  "Guido van Rossum and the Python community",
    },
    {
        "name":    "Tkinter",
        "version": "",
        "role":    "GUI toolkit (Python binding for Tcl/Tk)",
        "licence": "Tcl/Tk Licence (BSD-style)",
        "url":     "https://docs.python.org/3/library/tkinter.html",
        "author":  "Tcl Core Team",
    },
    {
        "name":    "VLC media player / libvlc",
        "version": "3.x",
        "role":    "Audio playback engine",
        "licence": "GNU Lesser General Public Licence v2.1 (LGPL)",
        "url":     "https://www.videolan.org/vlc",
        "author":  "VideoLAN organisation",
    },
    {
        "name":    "python-vlc",
        "version": "3.0+",
        "role":    "Python bindings for libvlc",
        "licence": "GNU Lesser General Public Licence v2.1 (LGPL)",
        "url":     "https://pypi.org/project/python-vlc",
        "author":  "Olivier Aubert",
    },
    {
        "name":    "Mutagen",
        "version": "1.47+",
        "role":    "Audio metadata / tag reading",
        "licence": "GNU General Public Licence v2 (GPL)",
        "url":     "https://mutagen.readthedocs.io",
        "author":  "Joe Wreschnig, Michael Urman, Christoph Reiter et al.",
    },
    {
        "name":    "Pillow (PIL Fork)",
        "version": "10+",
        "role":    "Image processing and display",
        "licence": "Historical Permission Notice and Disclaimer (HPND)",
        "url":     "https://python-pillow.org",
        "author":  "Alex Clark and contributors",
    },
    {
        "name":    "SQLite",
        "version": "3.x",
        "role":    "Music library and cover art database",
        "licence": "Public Domain",
        "url":     "https://www.sqlite.org",
        "author":  "D. Richard Hipp",
    },
    {
        "name":    "abcde",
        "version": "2.9+",
        "role":    "CD ripping (optional module)",
        "licence": "GNU General Public Licence v2 (GPL)",
        "url":     "https://abcde.einval.com",
        "author":  "Robert Woodcock, Jesus Climent et al.",
    },
    {
        "name":    "bluez / bluetoothctl",
        "version": "5.x",
        "role":    "Bluetooth device management (optional module)",
        "licence": "GNU General Public Licence v2 (GPL)",
        "url":     "http://www.bluez.org",
        "author":  "BlueZ project contributors",
    },
    {
        "name":    "PipeWire",
        "version": "1.x",
        "role":    "Audio server (Raspberry Pi OS Trixie/Bookworm)",
        "licence": "MIT / LGPL",
        "url":     "https://pipewire.org",
        "author":  "Wim Taymans and contributors",
    },
    {
        "name":    "Raspberry Pi OS",
        "version": "Trixie (Debian 13)",
        "role":    "Target operating system",
        "licence": "Various open-source licences",
        "url":     "https://www.raspberrypi.com/software",
        "author":  "Raspberry Pi Ltd",
    },
    {
        "name":    "MakeMKV",
        "version": "1.18+",
        "role":    "DVD/Blu-ray ripping (optional module)",
        "licence": "Proprietary (free beta)",
        "url":     "https://www.makemkv.com",
        "author":  "GuinpinSoft inc",
    },
    {
        "name":    "HandBrakeCLI",
        "version": "1.x",
        "role":    "Video transcoding (optional module)",
        "licence": "GNU General Public Licence v2 (GPL)",
        "url":     "https://handbrake.fr",
        "author":  "The HandBrake Team",
    },
    {
        "name":    "spotipy",
        "version": "2.x",
        "role":    "Spotify Web API client (optional module)",
        "licence": "MIT Licence",
        "url":     "https://spotipy.readthedocs.io",
        "author":  "Paul Lamere and contributors",
    },
    {
        "name":    "librespot / Raspotify",
        "version": "latest",
        "role":    "Spotify Connect receiver (optional module)",
        "licence": "MIT Licence",
        "url":     "https://github.com/dtcooper/raspotify",
        "author":  "David Cooper and contributors",
    },
    {
        "name":    "Radio Browser API",
        "version": "",
        "role":    "Internet radio station directory",
        "licence": "Creative Commons (CC0)",
        "url":     "https://www.radio-browser.info",
        "author":  "Community maintained",
    },
    {
        "name":    "TMDb API",
        "version": "",
        "role":    "Movie poster and metadata (optional)",
        "licence": "TMDb Terms of Use",
        "url":     "https://www.themoviedb.org",
        "author":  "The Movie Database",
    },
    {
        "name":    "Last.fm API",
        "version": "",
        "role":    "Music scrobbling (optional module)",
        "licence": "Last.fm API Terms",
        "url":     "https://www.last.fm/api",
        "author":  "Audioscrobbler / Last.fm",
    },
    {
        "name":    "gpiozero",
        "version": "2.x",
        "role":    "GPIO control for rotary encoder (optional module)",
        "licence": "BSD Licence",
        "url":     "https://gpiozero.readthedocs.io",
        "author":  "Ben Nuttall and Dave Jones",
    },
    {
        "name":    "Nuitka",
        "version": "2.x",
        "role":    "Python-to-native compiler (optional build)",
        "licence": "Apache Licence 2.0",
        "url":     "https://nuitka.net",
        "author":  "Kay Hayen",
    },
]


class AboutPage(tk.Frame):
    def __init__(self, parent, config=None, **kwargs):
        super().__init__(parent, bg=BG_DARK, **kwargs)
        self._config = config
        self._build()

    def _build(self):
        scroll = ScrollableFrame(self, bg=BG_DARK)
        scroll.pack(fill="both", expand=True)
        p = scroll.inner

        # ── Logo / title ──────────────────────────────────────────────
        tk.Frame(p, bg=BG_DARK, height=PAD).pack(fill="x")

        logo_frame = tk.Frame(p, bg=BG_DARK)
        logo_frame.pack(pady=(0, PAD))

        tk.Label(logo_frame,
                 text="Pulse", font=("Helvetica", int(FONT_DISPLAY[1] * 1.4), "italic"),
                 fg=NEON_PINK, bg=BG_DARK).pack(side="left")
        tk.Label(logo_frame,
                 text="Box", font=("Helvetica", int(FONT_DISPLAY[1] * 1.4), "bold"),
                 fg=NEON_BLUE, bg=BG_DARK).pack(side="left")

        tk.Label(p, text="J U K E B O X",
                 font=("Helvetica", FONT_BODY[1], "bold"),
                 fg=TEXT_PRIMARY, bg=BG_DARK).pack()

        tk.Label(p, text="Stream. Play. Enjoy.",
                 font=FONT_BODY, fg=ACCENT, bg=BG_DARK).pack(pady=(4, 0))

        tk.Label(p, text=f"Version {APP_VERSION}  ·  © {APP_YEAR}",
                 font=FONT_SMALL, fg=TEXT_PRIMARY, bg=BG_DARK).pack(pady=(2, PAD))

        tk.Frame(p, bg=ACCENT, height=2).pack(fill="x", padx=PAD * 4, pady=PAD)

        # ── Authors ───────────────────────────────────────────────────
        self._section(p, "AUTHORS")

        authors_frame = tk.Frame(p, bg=BG_PANEL, padx=PAD * 2, pady=PAD)
        authors_frame.pack(fill="x", padx=PAD * 3, pady=(0, PAD))

        self._author_row(authors_frame,
                         name="Andrew J. Morgan",
                         role="Creator & Lead Developer",
                         note="Original concept, architecture and implementation")

        self._section(p, "LICENCE")

        lic_frame = tk.Frame(p, bg=BG_PANEL, padx=PAD * 2, pady=PAD)
        lic_frame.pack(fill="x", padx=PAD * 3, pady=(0, PAD))

        tk.Label(lic_frame,
                 text="GNU General Public Licence, version 2 or later (GPL v2+)",
                 font=FONT_BODY, fg=ACCENT, bg=BG_PANEL, anchor="w").pack(fill="x")

        for line in LICENCE_TEXT.strip().splitlines():
            tk.Label(lic_frame, text=line,
                     font=FONT_SMALL, fg=TEXT_PRIMARY,
                     bg=BG_PANEL, anchor="w", wraplength=700,
                     justify="left").pack(fill="x", pady=1)

        # ── Credits ───────────────────────────────────────────────────
        self._section(p, "CREDITS & THIRD-PARTY SOFTWARE")

        tk.Label(p,
                 text="PulseBox is built on the shoulders of outstanding open-source projects:",
                 font=FONT_SMALL, fg=TEXT_PRIMARY,
                 bg=BG_DARK).pack(padx=PAD * 3, pady=(0, PAD_SM), anchor="w")

        for credit in CREDITS:
            self._credit_card(p, credit)

        # ── Acknowledgements ──────────────────────────────────────────
        self._section(p, "ACKNOWLEDGEMENTS")

        ack_frame = tk.Frame(p, bg=BG_PANEL, padx=PAD * 2, pady=PAD)
        ack_frame.pack(fill="x", padx=PAD * 3, pady=(0, PAD))

        acks = [
            "The VideoLAN team for VLC — the world's most versatile media player.",
            "The Raspberry Pi Foundation for making affordable, capable hardware.",
            "The Python Software Foundation and all Python contributors.",
            "The entire open-source community without whom none of this would be possible.",
        ]
        for ack in acks:
            row = tk.Frame(ack_frame, bg=BG_PANEL)
            row.pack(fill="x", pady=2)
            tk.Label(row, text="♦", font=FONT_TINY, fg=ACCENT,
                     bg=BG_PANEL).pack(side="left", padx=(0, 6))
            tk.Label(row, text=ack, font=FONT_SMALL, fg=TEXT_PRIMARY,
                     bg=BG_PANEL, anchor="w", wraplength=680,
                     justify="left").pack(side="left", fill="x")

        tk.Frame(p, bg=BG_DARK, height=PAD * 2).pack(fill="x")

        # Footer
        tk.Label(p,
                 text=f"PulseBox v{APP_VERSION}  ·  Built with ♥ for music lovers",
                 font=FONT_TINY, fg=TEXT_PRIMARY, bg=BG_DARK).pack(pady=PAD)

        scroll.bind_all_children_scroll(scroll.inner)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _section(self, parent, title: str):
        frame = tk.Frame(parent, bg=BG_DARK)
        frame.pack(fill="x", padx=PAD * 3, pady=(PAD, 4))
        tk.Label(frame, text=title, font=FONT_SUBHEAD,
                 fg=ACCENT2, bg=BG_DARK).pack(side="left")
        tk.Frame(frame, bg=BORDER, height=1).pack(
            side="left", fill="x", expand=True, padx=(8, 0), pady=6)

    def _author_row(self, parent, name, role, note=""):
        row = tk.Frame(parent, bg=BG_PANEL)
        row.pack(fill="x", pady=4)
        tk.Label(row, text=name, font=FONT_SUBHEAD,
                 fg=TEXT_PRIMARY, bg=BG_PANEL, width=24,
                 anchor="w").pack(side="left")
        info = tk.Frame(row, bg=BG_PANEL)
        info.pack(side="left", fill="x", expand=True)
        tk.Label(info, text=role, font=FONT_BODY,
                 fg=ACCENT, bg=BG_PANEL, anchor="w").pack(fill="x")
        if note:
            tk.Label(info, text=note, font=FONT_SMALL,
                     fg=TEXT_PRIMARY, bg=BG_PANEL, anchor="w").pack(fill="x")

    def _credit_card(self, parent, credit: dict):
        card = tk.Frame(parent, bg=BG_PANEL, padx=PAD, pady=PAD_SM)
        card.pack(fill="x", padx=PAD * 3, pady=2)

        # Name + version
        header = tk.Frame(card, bg=BG_PANEL)
        header.pack(fill="x")
        name_text = credit["name"]
        if credit.get("version"):
            name_text += f"  {credit['version']}"
        tk.Label(header, text=name_text, font=FONT_BODY,
                 fg=NEON_BLUE, bg=BG_PANEL, anchor="w").pack(side="left")

        # Licence badge
        tk.Label(header, text=credit.get("licence", ""),
                 font=FONT_TINY, fg=BG_DARK, bg=ACCENT2,
                 padx=4, pady=1).pack(side="right")

        # Role
        tk.Label(card, text=credit.get("role", ""),
                 font=FONT_SMALL, fg=TEXT_PRIMARY,
                 bg=BG_PANEL, anchor="w").pack(fill="x")

        # Author + URL
        meta = tk.Frame(card, bg=BG_PANEL)
        meta.pack(fill="x")
        if credit.get("author"):
            tk.Label(meta, text=f"by {credit['author']}",
                     font=FONT_TINY, fg=TEXT_PRIMARY,
                     bg=BG_PANEL, anchor="w").pack(side="left")
        if credit.get("url"):
            tk.Label(meta, text=credit["url"],
                     font=FONT_TINY, fg=TEXT_PRIMARY,
                     bg=BG_PANEL, anchor="w").pack(side="right")
